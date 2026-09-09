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

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class FullQaPipelineRunner {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();
    private static final Pattern NUMBER = Pattern.compile("-?\\d+(?:\\.\\d+)?");
    private static final Path PROJECT = locateProjectRoot();
    private static final Path DATA_DIR = Path.of(
            "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick");
    private static final Path RESULT_DIR = PROJECT.resolve("benchmark-results/full-pipeline-java");
    private static final Map<String, String> DOT_ENV = loadDotEnv(PROJECT.resolve(".env"));
    private static final String DEFAULT_USER_PREFIX = "benchmark-v107";

    private FullQaPipelineRunner() {}

    public static void main(String[] args) throws Exception {
        Options options = Options.parse(args);
        List<String> companies = selectedCompanies(options.company);
        Files.createDirectories(RESULT_DIR);
        List<ObjectNode> allResults = new ArrayList<>();

        for (String company : companies) {
            allResults.addAll(runCompany(company, options));
        }

        ObjectNode summary = JSON.createObjectNode();
        summary.put("total", allResults.size());
        summary.put("retrievePass", allResults.stream()
                .filter(record -> record.path("retrieveHttpStatus").asInt() == 200).count());
        summary.put("evidenceHit", allResults.stream()
                .filter(record -> record.path("evidenceHit").asBoolean()).count());
        summary.put("generationMatch", allResults.stream()
                .filter(record -> record.path("generatedAnswerMatch").asBoolean()).count());
        summary.put("abstentionQuestions", allResults.stream()
                .filter(record -> "abstention".equals(record.path("answerType").asText())).count());
        summary.put("topK", options.topK);
        summary.put("generationEnabled", !options.noGenerate);
        summary.put("testedAt", Instant.now().toString());
        Files.writeString(
                RESULT_DIR.resolve("summary.json"),
                JSON.writerWithDefaultPrettyPrinter().writeValueAsString(summary) + System.lineSeparator());

        System.out.println("\n=== 完整 Java 测试汇总 ===");
        System.out.println(JSON.writerWithDefaultPrettyPrinter().writeValueAsString(summary));
    }

    private static List<ObjectNode> runCompany(String company, Options options) throws Exception {
        Path datasetPath = DATA_DIR.resolve(company + ".json");
        JsonNode dataset = JSON.readTree(datasetPath.toFile());
        String companyId = dataset.path("company_id").asText();
        String userId = value("MEMIND_TEST_USER_PREFIX", DEFAULT_USER_PREFIX) + "-"
                + companyId + "-isolated";
        String agentId = value("MEMIND_TEST_AGENT_PREFIX", DEFAULT_USER_PREFIX) + "-"
                + companyId + "-isolated-agent";
        List<JsonNode> qaItems = nodes(dataset.path("qa_items"));
        if (options.maxQa > 0 && options.maxQa < qaItems.size()) {
            qaItems = qaItems.subList(0, options.maxQa);
        }

        Path resultPath = RESULT_DIR.resolve(companyId + "-full-results.jsonl");
        boolean detailedOutput = options.showAnswer || qaItems.size() <= 3;
        System.out.printf("%n=== %s (%s)，%d 道题 ===%n", company, companyId, qaItems.size());
        System.out.println("Detailed output: " + (detailedOutput ? "ON" : "OFF"));
        List<ObjectNode> results = new ArrayList<>();
        for (int i = 0; i < qaItems.size(); i++) {
            JsonNode qa = qaItems.get(i);
            ObjectNode result = runQuestion(company, companyId, userId, agentId, qa, options);
            Files.writeString(
                    resultPath,
                    JSON.writeValueAsString(result) + System.lineSeparator(),
                    java.nio.file.StandardOpenOption.CREATE,
                    java.nio.file.StandardOpenOption.APPEND);
            results.add(result);
            if (detailedOutput) {
                printQuestionResult(result, i + 1, qaItems.size());
            } else {
                printQuestionSummary(result, i + 1, qaItems.size());
            }
        }
        return results;
    }

    private static void printQuestionSummary(ObjectNode result, int index, int total) {
        System.out.printf("[%d/%d] %s retrieve=%s evidence=%s generation=%s%n",
                index,
                total,
                result.path("qaId").asText(),
                result.path("retrieveHttpStatus").asInt() == 200 ? "PASS" : "FAIL",
                result.path("evidenceHit").asBoolean() ? "HIT" : "MISS",
                result.path("generatedAnswerMatch").asBoolean() ? "MATCH" : "CHECK");
    }

    private static void printQuestionResult(ObjectNode result, int index, int total) {
        System.out.printf("%n[%d/%d] %s%n", index, total, result.path("qaId").asText());
        System.out.println("Question: " + result.path("question").asText());
        System.out.println("Expected: " + result.path("expectedAnswer").asText());
        System.out.println("Retrieve HTTP: " + result.path("retrieveHttpStatus").asInt());
        System.out.println("Evidence hit: " + (result.path("evidenceHit").asBoolean() ? "HIT" : "MISS")
                + " (rank=" + result.path("evidenceHitRank").asText("N/A") + ")");
        System.out.println("Generation HTTP: " + result.path("generationHttpStatus").asInt());
        JsonNode generated = result.path("generated");
        System.out.println("Answer: " + generated.path("answer").asText(""));
        System.out.println("Abstained: " + generated.path("abstained").asBoolean(false));
        System.out.println("Citations: " + generated.path("citations").toString());
        System.out.println("Reason: " + generated.path("reason").asText(""));
        System.out.println("Answer match: "
                + (result.path("generatedAnswerMatch").asBoolean() ? "MATCH" : "CHECK"));
        System.out.println("Evidence:");
        int rank = 1;
        for (JsonNode item : result.path("retrievedItems")) {
            System.out.printf("  #%d [vector=%.4f final=%.4f]: %s%n",
                    rank++, item.path("vectorScore").asDouble(),
                    item.path("finalScore").asDouble(), item.path("text").asText());
        }
    }

    private static ObjectNode runQuestion(
            String company,
            String companyId,
            String userId,
            String agentId,
            JsonNode qa,
            Options options) throws Exception {
        String question = qa.path("question").asText();
        Response retrieve = post(
                value("MEMIND_BASE_URL", "http://localhost:8366") + "/open/v1/memory/retrieve",
                retrieveBody(userId, agentId, question),
                null,
                options.timeoutSeconds);
        List<JsonNode> allItems = retrieve.status == 200
                ? nodes(JSON.readTree(retrieve.body).path("data").path("items"))
                : List.of();
        List<JsonNode> evidence = allItems.subList(0, Math.min(options.topK, allItems.size()));
        Hit hit = evidenceHit(qa.path("answer").asText(), qa.path("answer_type").asText(), evidence);

        int generationStatus = 0;
        ObjectNode generated = JSON.createObjectNode();
        if (!options.noGenerate && retrieve.status == 200) {
            Response generation = generateAnswer(question, qa.path("answer_type").asText(), evidence, options);
            generationStatus = generation.status;
            generated = parseGeneratedAnswer(generation.body);
        }

        ObjectNode record = JSON.createObjectNode();
        record.put("companyId", companyId);
        record.put("companyName", company);
        record.put("userId", userId);
        record.put("agentId", agentId);
        record.put("qaId", qa.path("qa_id").asText());
        record.put("question", question);
        record.put("expectedAnswer", qa.path("answer").asText());
        record.put("answerType", qa.path("answer_type").asText());
        record.put("retrieveHttpStatus", retrieve.status);
        record.put("evidenceHit", hit.hit);
        if (hit.rank == null) {
            record.putNull("evidenceHitRank");
        } else {
            record.put("evidenceHitRank", hit.rank);
        }
        record.put("evidenceCount", evidence.size());
        record.put("generationHttpStatus", generationStatus);
        record.set("generated", generated);
        record.put("generatedAnswerMatch", generatedMatches(
                qa.path("answer").asText(), qa.path("answer_type").asText(), generated));
        record.put("testedAt", Instant.now().toString());
        ArrayNode itemArray = record.putArray("retrievedItems");
        evidence.forEach(itemArray::add);
        return record;
    }

    private static Response generateAnswer(
            String question,
            String answerType,
            List<JsonNode> evidence,
            Options options) throws Exception {
        StringBuilder evidenceText = new StringBuilder();
        for (int i = 0; i < evidence.size(); i++) {
            JsonNode item = evidence.get(i);
            evidenceText.append('[').append(i + 1).append("] ")
                    .append(item.path("text").asText()).append('\n');
        }
        String prompt = """
                你是企业记忆问答系统。只能根据给定证据回答问题。

                严格要求：
                1. 不得使用证据之外的信息，不得猜测或补充常识。
                2. 不得混用不同公司的证据。
                3. 如果证据不足、公司不一致、年份不一致或无法确认，必须拒答。
                4. 如果存在更正前后版本，按问题要求的时间点回答。
                5. 只返回 JSON，不要 Markdown，不要额外解释。

                JSON 格式：
                {"answer":"最终回答；无法确认时填写无法确定","abstained":false,"citations":["1"],"reason":"简短说明"}

                问题：%s
                答案类型：%s
                证据：
                %s
                """.formatted(question, answerType, evidenceText);
        ObjectNode body = JSON.createObjectNode();
        body.put("model", options.model);
        body.putArray("messages").addObject()
                .put("role", "user")
                .put("content", prompt);
        body.put("temperature", 0.0).put("max_tokens", 500);
        return post(
                joinUrl(options.modelUrl, "chat/completions"),
                body,
                "Bearer " + options.apiKey,
                options.timeoutSeconds);
    }

    private static ObjectNode parseGeneratedAnswer(String body) throws IOException {
        JsonNode root = JSON.readTree(body);
        JsonNode choices = root.path("choices");
        if (!choices.isArray() || choices.isEmpty()) {
            return JSON.createObjectNode().put("answer", "").put("abstained", false)
                    .put("reason", "模型未返回 choices");
        }
        String content = choices.get(0).path("message").path("content").asText("").trim();
        content = content.replaceFirst("^```(?:json)?\\s*", "")
                .replaceFirst("\\s*```$", "").trim();
        try {
            JsonNode parsed = JSON.readTree(content);
            if (parsed != null && parsed.isObject()) {
                return (ObjectNode) parsed;
            }
        } catch (IOException ignored) {
            // Keep the raw model text for diagnosis when structured output is invalid.
        }
        return JSON.createObjectNode()
                .put("answer", content)
                .put("abstained", content.contains("无法确定"))
                .put("reason", "模型未按 JSON 返回");
    }

    private static boolean generatedMatches(String expected, String answerType, JsonNode generated) {
        String answer = generated.path("answer").asText("");
        if ("abstention".equals(answerType)) {
            return generated.path("abstained").asBoolean(false) || answer.contains("无法确定");
        }
        if (answer.isBlank()) {
            return false;
        }
        if ("number".equals(answerType)) {
            List<String> expectedNumbers = numbers(expected);
            List<String> actualNumbers = numbers(answer);
            return expectedNumbers.stream().anyMatch(actualNumbers::contains);
        }
        String normalizedExpected = normalize(expected);
        String normalizedAnswer = normalize(answer);
        return normalizedAnswer.contains(normalizedExpected)
                || tokensPresent(normalizedExpected, normalizedAnswer);
    }

    private static Hit evidenceHit(String expected, String answerType, List<JsonNode> items) {
        if ("abstention".equals(answerType)) {
            return new Hit(false, null);
        }
        String normalizedExpected = normalize(expected);
        for (int i = 0; i < items.size(); i++) {
            String text = items.get(i).path("text").asText();
            if ("number".equals(answerType)
                    && numbers(text).stream().anyMatch(numbers(expected)::contains)) {
                return new Hit(true, i + 1);
            }
            if (normalize(text).contains(normalizedExpected)
                    || tokensPresent(normalizedExpected, normalize(text))) {
                return new Hit(true, i + 1);
            }
        }
        return new Hit(false, null);
    }

    private static boolean tokensPresent(String expected, String actual) {
        String[] tokens = expected.split("[^\\p{L}\\p{N}.]+");
        int meaningful = 0;
        for (String token : tokens) {
            if (token.length() >= 2) {
                meaningful++;
                if (!actual.contains(token)) {
                    return false;
                }
            }
        }
        return meaningful > 0;
    }

    private static ObjectNode retrieveBody(String userId, String agentId, String query) {
        return JSON.createObjectNode().put("userId", userId).put("agentId", agentId)
                .put("query", query).put("strategy", "SIMPLE").put("trace", false);
    }

    private static Response post(String url, JsonNode body, String authorization, int timeoutSeconds)
            throws Exception {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .header("Content-Type", "application/json");
        if (authorization != null && !authorization.isBlank()) {
            builder.header("Authorization", authorization);
        }
        HttpResponse<String> response = HTTP.send(
                builder.POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(body))).build(),
                HttpResponse.BodyHandlers.ofString());
        return new Response(response.statusCode(), response.body());
    }

    private static String joinUrl(String baseUrl, String path) {
        return baseUrl.replaceAll("/+$", "") + "/" + path.replaceAll("^/+", "");
    }

    private static List<String> selectedCompanies(String company) {
        List<String> all = List.of(
                "鑫科精密零部件制造有限公司",
                "锐科航空装备股份有限公司",
                "鑫源精密机械制造有限公司",
                "联科绿筑新型建材有限公司",
                "绿能新源装备有限公司");
        if (company == null) {
            return all;
        }
        Set<String> wanted = new HashSet<>(List.of(company.split(",")));
        if (!all.containsAll(wanted)) {
            throw new IllegalArgumentException("Unknown company in --company: " + company);
        }
        return all.stream().filter(wanted::contains).toList();
    }

    private static List<JsonNode> nodes(JsonNode node) {
        List<JsonNode> result = new ArrayList<>();
        node.forEach(result::add);
        return result;
    }

    private static List<String> numbers(String value) {
        List<String> result = new ArrayList<>();
        Matcher matcher = NUMBER.matcher(value == null ? "" : value);
        while (matcher.find()) {
            result.add(matcher.group());
        }
        return result;
    }

    private static String normalize(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT)
                .replaceAll("[\\s,，。；;：:（）()\\[\\]{}\\\"'`]+", "");
    }

    private static String value(String name, String fallback) {
        String environment = System.getenv(name);
        if (environment != null && !environment.isBlank()) {
            return environment;
        }
        String dotenv = DOT_ENV.get(name);
        return dotenv == null || dotenv.isBlank() ? fallback : dotenv;
    }

    private static Path locateProjectRoot() {
        Path current = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        Path candidate = current;
        while (candidate != null) {
            if (Files.isRegularFile(candidate.resolve(".env"))
                    && Files.isDirectory(candidate.resolve("dev-tools"))) {
                return candidate;
            }
            candidate = candidate.getParent();
        }
        return current;
    }

    private static Map<String, String> loadDotEnv(Path path) {
        Map<String, String> values = new HashMap<>();
        if (!Files.isRegularFile(path)) {
            return values;
        }
        try {
            for (String line : Files.readAllLines(path)) {
                String trimmed = line.trim();
                if (trimmed.isBlank() || trimmed.startsWith("#")) {
                    continue;
                }
                if (trimmed.startsWith("export ")) {
                    trimmed = trimmed.substring("export ".length()).trim();
                }
                int equals = trimmed.indexOf('=');
                if (equals <= 0) {
                    continue;
                }
                String key = trimmed.substring(0, equals).trim();
                String value = trimmed.substring(equals + 1).trim();
                if ((value.startsWith("\"") && value.endsWith("\""))
                        || (value.startsWith("'") && value.endsWith("'"))) {
                    value = value.substring(1, value.length() - 1);
                }
                values.put(key, value);
            }
        } catch (IOException error) {
            throw new IllegalStateException("Unable to read .env: " + path, error);
        }
        return values;
    }

    private record Response(int status, String body) {}

    private record Hit(boolean hit, Integer rank) {}

    private record Options(
            String company,
            int maxQa,
            int topK,
            int timeoutSeconds,
            boolean noGenerate,
            boolean showAnswer,
            String modelUrl,
            String apiKey,
            String model) {
        static Options parse(String[] args) {
            String company = null;
            int maxQa = 0;
            int topK = 5;
            int timeoutSeconds = 300;
            boolean noGenerate = false;
            boolean showAnswer = false;
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--company" -> company = args[++i];
                    case "--max-qa" -> maxQa = Integer.parseInt(args[++i]);
                    case "--top-k" -> topK = Integer.parseInt(args[++i]);
                    case "--timeout" -> timeoutSeconds = Integer.parseInt(args[++i]);
                    case "--no-generate" -> noGenerate = true;
                    case "--show-answer" -> showAnswer = true;
                    default -> throw new IllegalArgumentException(
                            "Usage: [--company 公司[,公司]] [--max-qa N] [--top-k N] "
                                    + "[--timeout seconds] [--no-generate] [--show-answer]");
                }
            }
            if (maxQa < 0 || topK < 1 || timeoutSeconds < 1) {
                throw new IllegalArgumentException("--max-qa >= 0, --top-k > 0, --timeout > 0 required");
            }
            String modelUrl = value("OPENAI_BASE_URL", "");
            String apiKey = value("OPENAI_API_KEY", "");
            String model = value("OPENAI_CHAT_MODEL", "");
            if (!noGenerate && (modelUrl.isBlank() || apiKey.isBlank() || model.isBlank())) {
                throw new IllegalStateException(
                        "Missing model configuration in environment or .env: "
                                + "OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_CHAT_MODEL");
            }
            return new Options(company, maxQa, topK, timeoutSeconds, noGenerate, showAnswer,
                    modelUrl, apiKey, model);
        }
    }
}
