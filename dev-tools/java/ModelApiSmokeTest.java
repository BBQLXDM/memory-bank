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

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Locale;

public final class ModelApiSmokeTest {
    private static final Duration TIMEOUT = Duration.ofSeconds(60);
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();

    private ModelApiSmokeTest() {}

    public static void main(String[] args) throws Exception {
        String mode = args.length == 0 ? "all" : args[0].toLowerCase(Locale.ROOT);
        switch (mode) {
            case "chat" -> testChat();
            case "embedding" -> testEmbedding();
            case "all" -> {
                testChat();
                testEmbedding();
            }
            default -> {
                System.err.println("Usage: java ModelApiSmokeTest.java [all|chat|embedding]");
                System.exit(2);
            }
        }
    }

    private static void testChat() throws Exception {
        String baseUrl = requiredEnv("OPENAI_BASE_URL");
        String apiKey = requiredEnv("OPENAI_API_KEY");
        String model = requiredEnv("OPENAI_CHAT_MODEL");
        String prompt = envOrDefault("TEST_CHAT_PROMPT", "只回复 OK");
        String body = """
                {"model":"%s","messages":[{"role":"user","content":"%s"}],"temperature":0.2,"max_tokens":32}
                """.formatted(jsonEscape(model), jsonEscape(prompt)).trim();

        Response response = post(joinUrl(baseUrl, "chat/completions"), apiKey, body);
        report("Chat", model, response, "\"choices\"");
    }

    private static void testEmbedding() throws Exception {
        String baseUrl = requiredEnv("EMBEDDING_BASE_URL");
        String apiKey = requiredEnv("EMBEDDING_API_KEY");
        String model = requiredEnv("OPENAI_EMBEDDING_MODEL");
        String input = envOrDefault("TEST_EMBEDDING_INPUT", "Memind embedding connectivity test");
        String body = """
                {"model":"%s","input":["%s"],"encoding_format":"float"}
                """.formatted(jsonEscape(model), jsonEscape(input)).trim();

        Response response = post(joinUrl(baseUrl, "embeddings"), apiKey, body);
        report("Embedding", model, response, "\"embedding\"");
    }

    private static Response post(String url, String apiKey, String body) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .timeout(TIMEOUT)
                .header("Authorization", "Bearer " + apiKey)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
        return new Response(response.statusCode(), response.body());
    }

    private static void report(String name, String model, Response response, String successMarker) {
        boolean passed = response.statusCode() >= 200
                && response.statusCode() < 300
                && response.body().contains(successMarker);
        System.out.printf("%n--- %s test ---%n", name);
        System.out.println("Model: " + model);
        System.out.println("HTTP status: " + response.statusCode());
        System.out.println("Result: " + (passed ? "PASS" : "FAIL"));
        System.out.println("Response: " + abbreviate(response.body(), 1_200));
        if (!passed) {
            throw new IllegalStateException(name + " test failed");
        }
    }

    private static String requiredEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException("Missing environment variable: " + name);
        }
        return value;
    }

    private static String envOrDefault(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String joinUrl(String baseUrl, String path) {
        return baseUrl.replaceAll("/+$", "") + "/" + path.replaceAll("^/+", "");
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    private static String abbreviate(String value, int maxLength) {
        if (value == null) {
            return "";
        }
        String singleLine = value.replace('\n', ' ').replace('\r', ' ');
        return singleLine.length() <= maxLength
                ? singleLine
                : singleLine.substring(0, maxLength) + "... [truncated]";
    }

    private record Response(int statusCode, String body) {}
}
