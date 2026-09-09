/*
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.openmemind.ai.memory.server.service.memory;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.Base64Source;
import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.ContentBlock;
import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.ImageBlock;
import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.Message;
import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.TextBlock;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Enriches incoming messages by transcribing inline base64 image blocks into text
 * via an OpenAI-compatible vision endpoint, so downstream extraction (chunk → item)
 * can capture image facts that would otherwise be dropped by text-only formatting.
 *
 * <p>Failures are non-fatal: on any error the original message is returned unchanged.
 */
@Component
public class MessageImageEnricher {

    private static final Logger log = LoggerFactory.getLogger(MessageImageEnricher.class);

    private static final String DESCRIPTION_PROMPT =
            "请忠实转写图片中的全部关键信息（企业名称、数值、金额、比例、日期、编号、名单、状态等），"
                    + "按“字段: 值”的形式逐项列出，保持原文数值与用词，不要推测、不要总结、不要遗漏数字。";

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value(
            "${memind.ai.vision.base-url:${OPENAI_BASE_URL:https://dashscope.aliyuncs.com/compatible-mode/v1}}")
    private String baseUrl;

    @Value("${memind.ai.vision.model:${MEMIND_VISION_MODEL:qwen-vl-plus}}")
    private String model;

    @Value("${memind.ai.vision.api-key:${OPENAI_API_KEY:}}")
    private String apiKey;

    @Value("${memind.ai.vision.timeout-seconds:${MEMIND_VISION_TIMEOUT_SECONDS:60}}")
    private long timeoutSeconds;

    @Value("${memind.ai.vision.enabled:${MEMIND_VISION_ENABLED:true}}")
    private boolean enabled;

    public MessageImageEnricher() {
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    }

    /**
     * Returns a message where every inline base64 image block is followed by an
     * extra TextBlock containing the vision transcription. Original message is
     * returned unchanged when there are no images, the feature is disabled, or
     * transcription fails.
     */
    public Message enrich(Message message) {
        if (!enabled || message == null || message.content() == null) {
            return message;
        }
        List<ContentBlock> blocks = message.content();
        boolean hasImage = blocks.stream().anyMatch(ImageBlock.class::isInstance);
        if (!hasImage) {
            return message;
        }

        List<ContentBlock> enriched = new ArrayList<>(blocks.size() + 2);
        int described = 0;
        for (ContentBlock block : blocks) {
            enriched.add(block);
            if (block instanceof ImageBlock imageBlock
                    && imageBlock.getSource() instanceof Base64Source source) {
                String description = describeImage(source.getMediaType(), source.getData());
                if (description != null && !description.isBlank()) {
                    enriched.add(
                            TextBlock.builder().text("（图片内容：" + description.trim() + "）").build());
                    described++;
                }
            }
        }
        if (described == 0) {
            return message;
        }
        log.info("MessageImageEnricher: transcribed {} image block(s) into text", described);
        return new Message(
                message.role(),
                enriched,
                message.timestamp(),
                message.userName(),
                message.sourceClient());
    }

    private String describeImage(String mediaType, String base64Data) {
        if (base64Data == null || base64Data.isBlank()) {
            return null;
        }
        try {
            // data URI 要求原始字节长度，这里直接用输入的 base64
            byte[] decoded = Base64.getDecoder().decode(base64Data);
            String dataUri =
                    "data:"
                            + (mediaType == null ? "image/png" : mediaType)
                            + ";base64,"
                            + base64Data;

            ObjectNode root = objectMapper.createObjectNode();
            root.put("model", model);
            root.put("temperature", 0.1);
            ArrayNode messages = root.putArray("messages");
            ObjectNode userMessage = messages.addObject();
            userMessage.put("role", "user");
            ArrayNode content = userMessage.putArray("content");
            ObjectNode textPart = content.addObject();
            textPart.put("type", "text");
            textPart.put("text", DESCRIPTION_PROMPT);
            ObjectNode imagePart = content.addObject();
            imagePart.put("type", "image_url");
            imagePart.putObject("image_url").put("url", dataUri);
            root.put("max_tokens", 800);

            HttpRequest request =
                    HttpRequest.newBuilder()
                            .uri(URI.create(baseUrl.replaceAll("/+$", "") + "/chat/completions"))
                            .timeout(Duration.ofSeconds(timeoutSeconds))
                            .header("Content-Type", "application/json")
                            .header("Authorization", "Bearer " + apiKey)
                            .POST(
                                    HttpRequest.BodyPublishers.ofString(
                                            objectMapper.writeValueAsString(root)))
                            .build();

            HttpResponse<String> response =
                    httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                log.warn(
                        "MessageImageEnricher: vision call failed http={} body={}",
                        response.statusCode(),
                        abbreviate(response.body()));
                return null;
            }
            JsonNode node = objectMapper.readTree(response.body());
            JsonNode contentNode = node.path("choices").path(0).path("message").path("content");
            String text = contentNode.isMissingNode() ? null : contentNode.asText(null);
            log.info(
                    "MessageImageEnricher: vision ok bytes={} replyChars={}",
                    decoded.length,
                    text == null ? 0 : text.length());
            return text;
        } catch (Exception e) {
            log.warn("MessageImageEnricher: vision transcription failed: {}", e.getMessage());
            return null;
        }
    }

    private String abbreviate(String value) {
        if (value == null) {
            return "";
        }
        return value.length() <= 300 ? value : value.substring(0, 300);
    }
}
