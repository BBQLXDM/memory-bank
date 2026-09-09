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

public final class MemindApiSmokeTest {
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();
    private static final Duration SHORT_TIMEOUT = Duration.ofSeconds(15);
    private static final Duration MODEL_TIMEOUT = Duration.ofMinutes(5);

    private static final String BASE_URL = env("MEMIND_BASE_URL", "http://localhost:8366");
    private static final String USER_ID = env("MEMIND_TEST_USER_ID", "local-test-user");
    private static final String AGENT_ID = env("MEMIND_TEST_AGENT_ID", "local-test-agent");
    private static final String SOURCE_CLIENT = env("MEMIND_TEST_SOURCE_CLIENT", "java-smoke-test");
    private static final String MEMORY = env(
            "MEMIND_TEST_MEMORY", "我喜欢使用 Python 编程，并且通常在周日上午学习新技术。");
    private static final String QUERY = env(
            "MEMIND_TEST_QUERY", "用户喜欢使用什么编程语言，通常什么时候学习新技术？");
    private static final String TEXT_FIELD = "\"text\":\"";

    private MemindApiSmokeTest() {}

    public static void main(String[] args) throws Exception {
        String mode = args.length == 0 ? "all" : args[0].toLowerCase(Locale.ROOT);
        switch (mode) {
            case "health" -> health();
            case "extract" -> extract();
            case "retrieve" -> retrieve();
            case "dashboard" -> dashboard();
            case "all" -> {
                health();
                extract();
                retrieve();
                dashboard();
            }
            default -> {
                System.err.println(
                        "Usage: java MemindApiSmokeTest.java [all|health|extract|retrieve|dashboard]");
                System.exit(2);
            }
        }
    }

    private static void health() throws Exception {
        Response response = get("/open/v1/health", SHORT_TIMEOUT);
        report("Health", response, "\"status\":\"UP\"");
    }

    private static void extract() throws Exception {
        String body = """
                {
                  "userId":"%s",
                  "agentId":"%s",
                  "sourceClient":"%s",
                  "rawContent":{
                    "type":"conversation",
                    "messages":[
                      {
                        "role":"USER",
                        "content":[{"type":"text","text":"%s"}]
                      },
                      {
                        "role":"ASSISTANT",
                        "content":[{"type":"text","text":"好的，我会记住这项信息。"}]
                      }
                    ]
                  }
                }
                """.formatted(
                        json(USER_ID), json(AGENT_ID), json(SOURCE_CLIENT), json(MEMORY));
        Response response = post("/open/v1/memory/sync/extract", body, MODEL_TIMEOUT);
        report("Extract", response, "\"rawDataIds\"");
    }

    private static void retrieve() throws Exception {
        String body = """
                {
                  "userId":"%s",
                  "agentId":"%s",
                  "query":"%s",
                  "strategy":"SIMPLE",
                  "trace":true
                }
                """.formatted(json(USER_ID), json(AGENT_ID), json(QUERY));
        Response response = post("/open/v1/memory/retrieve", body, MODEL_TIMEOUT);
        reportRetrieve(response);
    }

    private static void reportRetrieve(Response response) {
        boolean passed = response.statusCode() >= 200
                && response.statusCode() < 300
                && response.body().contains("\"items\"");
        System.out.printf("%n--- Retrieve test ---%n");
        System.out.println("Base URL: " + BASE_URL);
        System.out.println("HTTP status: " + response.statusCode());
        System.out.println("Result: " + (passed ? "PASS" : "FAIL"));
        if (!passed) {
            System.out.println("Error: " + abbreviate(response.body(), 1_200));
            throw new IllegalStateException("Retrieve test failed");
        }

        String itemsJson = extractJsonArray(response.body(), "\"items\"");
        int count = 0;
        int searchFrom = 0;
        System.out.println("Retrieved items:");
        while (true) {
            int fieldStart = itemsJson.indexOf(TEXT_FIELD, searchFrom);
            if (fieldStart < 0) {
                break;
            }
            int valueStart = fieldStart + TEXT_FIELD.length();
            int valueEnd = findJsonStringEnd(itemsJson, valueStart);
            if (valueEnd < 0) {
                break;
            }
            count++;
            System.out.printf("  %d.%n     %s%n", count,
                    unescape(itemsJson.substring(valueStart, valueEnd)));
            searchFrom = valueEnd + 1;
        }
        System.out.println("Item count: " + count);
        if (count == 0) {
            System.out.println("  No memory items were returned.");
        }
        System.out.println("Raw JSON hidden; set MEMIND_TEST_VERBOSE=true to show it.");
        if (Boolean.parseBoolean(env("MEMIND_TEST_VERBOSE", "false"))) {
            System.out.println("Response: " + response.body());
        }
    }

    private static String extractJsonArray(String json, String fieldName) {
        int fieldStart = json.indexOf(fieldName);
        int arrayStart = fieldStart < 0 ? -1 : json.indexOf('[', fieldStart + fieldName.length());
        if (arrayStart < 0) {
            return "[]";
        }
        int depth = 0;
        boolean inString = false;
        boolean escaped = false;
        for (int i = arrayStart; i < json.length(); i++) {
            char ch = json.charAt(i);
            if (inString) {
                if (escaped) {
                    escaped = false;
                } else if (ch == '\\') {
                    escaped = true;
                } else if (ch == '"') {
                    inString = false;
                }
            } else if (ch == '"') {
                inString = true;
            } else if (ch == '[') {
                depth++;
            } else if (ch == ']' && --depth == 0) {
                return json.substring(arrayStart, i + 1);
            }
        }
        return "[]";
    }

    private static int findJsonStringEnd(String json, int valueStart) {
        boolean escaped = false;
        for (int i = valueStart; i < json.length(); i++) {
            char ch = json.charAt(i);
            if (escaped) {
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                return i;
            }
        }
        return -1;
    }

    private static String unescape(String value) {
        return value.replace("\\\\n", "\\n")
                .replace("\\\\r", "\\r")
                .replace("\\\\t", "\\t")
                .replace("\\\\\"", "\"")
                .replace("\\\\\\\\", "\\\\");
    }

    private static void dashboard() throws Exception {
        Response response = get("/admin/v1/dashboard", SHORT_TIMEOUT);
        report("Dashboard", response, "\"totals\"");
    }

    private static Response get(String path, Duration timeout) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(uri(path))
                .timeout(timeout)
                .GET()
                .build();
        return send(request);
    }

    private static Response post(String path, String body, Duration timeout) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(uri(path))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return send(request);
    }

    private static Response send(HttpRequest request) throws Exception {
        HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
        return new Response(response.statusCode(), response.body());
    }

    private static URI uri(String path) {
        return URI.create(BASE_URL.replaceAll("/+$", "") + "/" + path.replaceAll("^/+", ""));
    }

    private static void report(String name, Response response, String marker) {
        boolean passed = response.statusCode() >= 200
                && response.statusCode() < 300
                && response.body().contains(marker);
        System.out.printf("%n--- %s test ---%n", name);
        System.out.println("Base URL: " + BASE_URL);
        System.out.println("HTTP status: " + response.statusCode());
        System.out.println("Result: " + (passed ? "PASS" : "FAIL"));
        System.out.println("Response: " + abbreviate(response.body(), 2_000));
        if (!passed) {
            throw new IllegalStateException(name + " test failed");
        }
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String json(String value) {
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
