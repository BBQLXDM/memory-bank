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
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

public final class MemindBenchmarkRunner {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();
    private static final Duration TIMEOUT = Duration.ofMinutes(5);

    private MemindBenchmarkRunner() {}

    public static void main(String[] args) throws Exception {
        long runStartedNanos = System.nanoTime();
        Options options = Options.parse(args);
        JsonNode sample = JSON.readTree(Path.of(options.file).toFile());
        String userId = options.userId != null
                ? options.userId
                : "benchmark-v107-" + sample.path("company_id").asText();
        List<JsonNode> sessions = nodes(sample.path("context").path("sessions"));
        List<JsonNode> qaItems = selectedQaItems(sample, options);
        List<JsonNode> ingestSessions = sessionsForIngest(sessions, qaItems, options);
        int requiredSessionIndex = requiredSessionIndex(sessions, qaItems);
        int sessionCount = options.ingestPolicy.equals("evidence-only")
                ? ingestSessions.size()
                : options.qaId == null
                        ? Math.min(options.maxSessions, sessions.size())
                        : requiredSessionIndex + 1;
        int startSessionIndex = options.ingestPolicy.equals("evidence-only")
                ? 0
                : resumeStartIndex(sessions, options.resumeAfter);

        System.out.printf("Dataset: %s%nCompany: %s (%s)%nSessions: %d..%d%nSelected QA: %d%nMode: %s%n%n",
                options.file,
                sample.path("company_name").asText(),
                userId,
                startSessionIndex + 1,
                sessionCount,
                qaItems.size(),
                options.mode + " / " + options.ingestPolicy);

        if (options.mode.equals("ingest") || options.mode.equals("all")) {
            java.util.Set<String> completedSessions = loadCompletedSessions(sample, userId, options);
            for (int i = startSessionIndex; i < sessionCount; i++) {
                JsonNode session = ingestSessions.get(i - startSessionIndex);
                String sessionId = session.path("session_id").asText();
                if (completedSessions.contains(sessionId)) {
                    System.out.printf("INGEST session=%s result=SKIP (already completed)%n", sessionId);
                    continue;
                }
                long sessionStartedNanos = System.nanoTime();
                String path = options.asyncIngest
                        ? "/open/v1/memory/async/extract"
                        : "/open/v1/memory/sync/extract";
                Response response = post(
                        path,
                        extractBody(
                                userId,
                                options.agentId,
                                sample.path("company_id").asText(),
                                session));
                boolean ok = options.asyncIngest
                        ? response.status == 202
                        : response.status >= 200 && response.status < 300;
                System.out.printf("INGEST session=%s mode=%s status=%d result=%s elapsed=%s%n",
                        session.path("session_id").asText(),
                        options.asyncIngest ? "async" : "sync",
                        response.status,
                        ok ? "PASS" : "FAIL",
                        formatDuration(sessionStartedNanos));
                if (!ok) {
                    System.out.println(abbreviate(response.body, 500));
                    throw new IllegalStateException("Ingest failed");
                }
                if (options.asyncIngest) {
                    JsonNode targetQa = qaItems.size() == 1 ? qaItems.get(0) : null;
                    boolean completed = waitForAsyncCompletion(
                            userId,
                            options.agentId,
                            targetQa,
                            sample.path("company_id").asText(),
                            session.path("session_id").asText(),
                            options);
                    if (!completed) {
                        throw new IllegalStateException(
                                "Async ingest did not produce memory items: "
                                        + session.path("session_id").asText());
                    }
                }
                recordCompletedSession(sample, session, userId, options);
            }
        }

        if (options.mode.equals("query") || options.mode.equals("all")) {
            int queryCount = options.qaId == null
                    ? Math.min(options.maxQa, qaItems.size())
                    : qaItems.size();
            for (int i = 0; i < queryCount; i++) {
                long queryStartedNanos = System.nanoTime();
                JsonNode qa = qaItems.get(i);
                Response response = post("/open/v1/memory/retrieve",
                        retrieveBody(userId, options.agentId, qa.path("question").asText()));
                boolean ok = response.status >= 200 && response.status < 300;
                QueryEvaluation evaluation = printQueryResult(qa, response, ok);
                long queryElapsedMillis =
                        Duration.ofNanos(System.nanoTime() - queryStartedNanos).toMillis();
                System.out.println("Query elapsed: " + formatDuration(queryStartedNanos));
                recordQaResult(sample, qa, userId, response, evaluation, queryElapsedMillis, options);
                if (!ok) {
                    throw new IllegalStateException("Query failed: " + qa.path("qa_id").asText());
                }
            }
        }
        System.out.println("\nTotal elapsed: " + formatDuration(runStartedNanos));
    }

    private static ObjectNode extractBody(
            String userId, String agentId, String companyId, JsonNode session) {
        ObjectNode body = JSON.createObjectNode();
        body.put("userId", userId)
                .put("agentId", agentId)
                .put(
                        "sourceClient",
                        "benchmark-v107:"
                                + companyId
                                + ":"
                                + session.path("session_id").asText());
        ObjectNode raw = body.putObject("rawContent").put("type", "conversation");
        ArrayNode messages = raw.putArray("messages");
        for (JsonNode turn : nodes(session.path("turns"))) {
            ObjectNode message = messages.addObject();
            String role = "assistant".equalsIgnoreCase(turn.path("role").asText()) ? "ASSISTANT" : "USER";
            message.put("role", role);
            message.putArray("content").addObject().put("type", "text").put("text", turn.path("content").asText());
        }
        return body;
    }

    private static ObjectNode retrieveBody(String userId, String agentId, String query) {
        return JSON.createObjectNode().put("userId", userId).put("agentId", agentId)
                .put("query", query).put("strategy", "SIMPLE").put("trace", false);
    }

    private static ObjectNode itemsQueryBody(String userId, String agentId) {
        return JSON.createObjectNode().put("userId", userId).put("agentId", agentId)
                .put("limit", 1);
    }

    private static ObjectNode rawDataQueryBody(
            String userId, String agentId, String companyId, String sessionId) {
        ObjectNode body = JSON.createObjectNode().put("userId", userId).put("agentId", agentId)
                .put("limit", 1);
        body.putArray("sourceClients").add("benchmark-v107:" + companyId + ":" + sessionId);
        return body;
    }

    private static List<JsonNode> rawDataRecords(String responseBody) throws Exception {
        JsonNode data = JSON.readTree(responseBody).path("data");
        JsonNode records = data.has("rawData") ? data.path("rawData") : data.path("records");
        return nodes(records);
    }

    private static List<JsonNode> selectedQaItems(JsonNode sample, Options options) {
        List<JsonNode> all = nodes(sample.path("qa_items"));
        if (options.qaId == null) {
            return all;
        }
        return all.stream()
                .filter(qa -> options.qaId.equals(qa.path("qa_id").asText()))
                .toList();
    }

    private static List<JsonNode> sessionsForIngest(
            List<JsonNode> sessions, List<JsonNode> qaItems, Options options) {
        if (!options.ingestPolicy.equals("evidence-only")) {
            return sessions;
        }
        if (qaItems.size() != 1) {
            throw new IllegalArgumentException("evidence-only requires exactly one --qa-id");
        }
        JsonNode evidence = qaItems.get(0).path("evidence");
        List<String> sessionIds = new ArrayList<>();
        evidence.forEach(ref -> {
            String sessionId = ref.path("session_id").asText();
            if (!sessionId.isBlank() && !sessionIds.contains(sessionId)) {
                sessionIds.add(sessionId);
            }
        });
        if (sessionIds.isEmpty()) {
            throw new IllegalArgumentException("Selected QA has no evidence session");
        }
        List<JsonNode> selected = sessions.stream()
                .filter(session -> sessionIds.contains(session.path("session_id").asText()))
                .toList();
        if (selected.size() != sessionIds.size()) {
            throw new IllegalArgumentException("Evidence references an unknown session");
        }
        return selected;
    }

    private static int requiredSessionIndex(List<JsonNode> sessions, List<JsonNode> qaItems) {
        if (qaItems.size() != 1) {
            return -1;
        }
        String afterSession = qaItems.get(0).path("query_time").path("after_session").asText();
        if (afterSession.isBlank()) {
            return sessions.size() - 1;
        }
        for (int i = 0; i < sessions.size(); i++) {
            if (afterSession.equals(sessions.get(i).path("session_id").asText())) {
                return i;
            }
        }
        throw new IllegalArgumentException("QA references unknown after_session: " + afterSession);
    }

    private static int resumeStartIndex(List<JsonNode> sessions, String resumeAfter) {
        if (resumeAfter == null) {
            return 0;
        }
        for (int i = 0; i < sessions.size(); i++) {
            if (resumeAfter.equals(sessions.get(i).path("session_id").asText())) {
                return i + 1;
            }
        }
        throw new IllegalArgumentException("Unknown --resume-after session: " + resumeAfter);
    }

    private static QueryEvaluation printQueryResult(
            JsonNode qa, Response response, boolean httpOk) throws Exception {
        System.out.printf("%n=== QA %s ===%n", qa.path("qa_id").asText());
        System.out.println("Capability: " + qa.path("capability").asText());
        System.out.println("Answer type: " + qa.path("answer_type").asText());
        System.out.println("Question: " + qa.path("question").asText());
        System.out.println("Expected: " + qa.path("answer").asText());
        System.out.println("HTTP: " + response.status + " " + (httpOk ? "PASS" : "FAIL"));
        if (!httpOk) {
            System.out.println("Error: " + abbreviate(response.body, 500));
            return new QueryEvaluation("HTTP_FAIL", List.of(), List.of());
        }

        JsonNode data = JSON.readTree(response.body).path("data");
        List<String> texts = new ArrayList<>();
        List<JsonNode> retrievedItems = new ArrayList<>();
        int rank = 1;
        for (JsonNode item : nodes(data.path("items"))) {
            String text = item.path("text").asText();
            texts.add(text);
            retrievedItems.add(item);
            System.out.printf("Item #%d [vector=%.4f final=%.4f]: %s%n",
                    rank++, item.path("vectorScore").asDouble(),
                    item.path("finalScore").asDouble(), text);
        }

        boolean abstention = "abstention".equals(qa.path("answer_type").asText());
        boolean answerHit = !abstention
                && answerHit(
                        qa.path("answer").asText(), qa.path("answer_type").asText(), texts);
        String status = abstention
                ? "MANUAL_ABSTENTION_REVIEW"
                : answerHit ? "ANSWER_HIT" : "NO_LITERAL_HIT";
        System.out.println("Retrieved items: " + texts.size());
        System.out.println("Basic evaluation: " + status);

        JsonNode rawData = data.path("rawData");
        if (rawData.isArray() && !rawData.isEmpty()) {
            String caption = rawData.get(0).path("caption").asText();
            System.out.println("Caption language: " + languageOf(caption));
            System.out.println("Caption: " + abbreviate(caption, 240));
        }
        return new QueryEvaluation(status, retrievedItems, nodes(data.path("rawData")));
    }

    private static void recordQaResult(
            JsonNode sample,
            JsonNode qa,
            String userId,
            Response response,
            QueryEvaluation evaluation,
            long queryElapsedMillis,
            Options options) throws Exception {
        Path resultPath = qaResultPath(options);
        Files.createDirectories(resultPath.getParent());
        ObjectNode record = JSON.createObjectNode();
        record.put("companyId", sample.path("company_id").asText());
        record.put("companyName", sample.path("company_name").asText());
        record.put("userId", userId);
        record.put("agentId", options.agentId);
        record.put("qaId", qa.path("qa_id").asText());
        record.put("question", qa.path("question").asText());
        record.put("expectedAnswer", qa.path("answer").asText());
        record.put("answerType", qa.path("answer_type").asText());
        record.put("httpStatus", response.status);
        record.put("evaluation", evaluation.status());
        record.put("queryElapsedMillis", queryElapsedMillis);
        record.put("testedAt", java.time.Instant.now().toString());
        ArrayNode items = record.putArray("retrievedItems");
        evaluation.items().forEach(items::add);
        ArrayNode rawData = record.putArray("rawData");
        evaluation.rawData().forEach(rawData::add);
        Files.writeString(
                resultPath,
                JSON.writeValueAsString(record) + System.lineSeparator(),
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND);
        System.out.println("Result recorded: " + resultPath);
    }

    private static Path qaResultPath(Options options) {
        if (options.resultFile != null) {
            return Path.of(options.resultFile).toAbsolutePath();
        }
        return Path.of(System.getProperty("user.dir"))
                .resolve("benchmark-results/qa-results.jsonl")
                .toAbsolutePath();
    }

    private static boolean waitForAsyncCompletion(
            String userId,
            String agentId,
            JsonNode qa,
            String companyId,
            String sessionId,
            Options options) throws Exception {
        if (qa == null) {
            long startedNanos = System.nanoTime();
            long deadlineNanos = startedNanos + Duration.ofSeconds(options.waitSeconds).toNanos();
            System.out.printf("  polling items every %d seconds, timeout=%d seconds%n",
                    options.pollSeconds, options.waitSeconds);
            while (System.nanoTime() < deadlineNanos) {
                Thread.sleep(Duration.ofSeconds(options.pollSeconds).toMillis());
                Response response = post("/open/v1/memory/raw-data/query",
                        rawDataQueryBody(userId, agentId, companyId, sessionId));
                if (response.status >= 200 && response.status < 300
                        && !rawDataRecords(response.body).isEmpty()) {
                    System.out.printf("  async result ready after %s%n",
                            formatDuration(startedNanos));
                    return true;
                }
            }
            System.out.printf("  polling timeout after %s%n", formatDuration(startedNanos));
            return false;
        }
        long startedNanos = System.nanoTime();
        long deadlineNanos = startedNanos + Duration.ofSeconds(options.waitSeconds).toNanos();
        System.out.printf("  polling every %d seconds, timeout=%d seconds%n",
                options.pollSeconds, options.waitSeconds);
        while (System.nanoTime() < deadlineNanos) {
            Thread.sleep(Duration.ofSeconds(options.pollSeconds).toMillis());
            Response response = post("/open/v1/memory/retrieve",
                    retrieveBody(userId, agentId, qa.path("question").asText()));
            if (response.status >= 200 && response.status < 300) {
                List<String> texts = itemTexts(response.body);
                if (!texts.isEmpty()) {
                    System.out.printf(
                            "  async result ready after %s (%d items)%n",
                            formatDuration(startedNanos), texts.size());
                    return true;
                }
            }
        }
        System.out.printf("  polling timeout after %s%n", formatDuration(startedNanos));
        return false;
    }

    private static java.util.Set<String> loadCompletedSessions(
            JsonNode sample, String userId, Options options) throws Exception {
        Path path = progressPath(options);
        if (!Files.isRegularFile(path)) {
            return java.util.Set.of();
        }
        java.util.Set<String> completed = new java.util.HashSet<>();
        for (String line : Files.readAllLines(path)) {
            if (line.isBlank()) {
                continue;
            }
            JsonNode record = JSON.readTree(line);
            if (sample.path("company_id").asText().equals(record.path("companyId").asText())
                    && userId.equals(record.path("userId").asText())
                    && options.agentId.equals(record.path("agentId").asText())) {
                completed.add(record.path("sessionId").asText());
            }
        }
        return completed;
    }

    private static void recordCompletedSession(
            JsonNode sample, JsonNode session, String userId, Options options) throws Exception {
        Path progressPath = progressPath(options);
        Files.createDirectories(progressPath.getParent());
        ObjectNode record = JSON.createObjectNode();
        record.put("companyId", sample.path("company_id").asText());
        record.put("companyName", sample.path("company_name").asText());
        record.put("userId", userId);
        record.put("agentId", options.agentId);
        record.put("sessionId", session.path("session_id").asText());
        record.put("ingestPolicy", options.ingestPolicy);
        record.put("completedAt", java.time.Instant.now().toString());
        Files.writeString(
                progressPath,
                JSON.writeValueAsString(record) + System.lineSeparator(),
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND);
        System.out.println("  progress recorded: " + progressPath);
    }

    private static Path progressPath(Options options) {
        if (options.progressFile != null) {
            return Path.of(options.progressFile).toAbsolutePath();
        }
        Path project = Path.of(System.getProperty("user.dir"));
        return project.resolve("benchmark-results/completed-sessions.jsonl").toAbsolutePath();
    }

    private static List<String> itemTexts(String responseBody) throws Exception {
        List<String> texts = new ArrayList<>();
        JsonNode root = JSON.readTree(responseBody).path("data");
        JsonNode items = root.has("items") ? root.path("items") : root.path("records");
        for (JsonNode item : nodes(items)) {
            texts.add(item.path("text").asText());
        }
        return texts;
    }

    private static boolean answerHit(String expected, String answerType, List<String> texts) {
        String combined = String.join(" ", texts);
        if ("number".equals(answerType)) {
            String expectedNumber = firstNumber(expected);
            return !expectedNumber.isBlank() && firstNumber(combined).equals(expectedNumber);
        }
        String normalizedExpected = normalize(expected);
        String normalizedCombined = normalize(combined);
        if (normalizedExpected.isBlank()) {
            return false;
        }
        if (normalizedCombined.contains(normalizedExpected)) {
            return true;
        }
        for (String token : normalizedExpected.split("[^\\p{L}\\p{N}.]+")) {
            if (token.length() >= 2 && normalizedCombined.contains(token)) {
                return true;
            }
        }
        return false;
    }

    private static String firstNumber(String value) {
        if (value == null) {
            return "";
        }
        var matcher = java.util.regex.Pattern.compile("-?\\d+(?:\\.\\d+)?").matcher(value);
        return matcher.find() ? matcher.group() : "";
    }

    private static String normalize(String value) {
        return value == null ? "" : value.toLowerCase().replaceAll("[\\s,，。；;：:（）()]+", "");
    }

    private static String languageOf(String value) {
        if (value == null || value.isBlank()) {
            return "EMPTY";
        }
        long chinese = value.codePoints().filter(c -> c >= 0x4E00 && c <= 0x9FFF).count();
        long latin = value.codePoints().filter(c -> (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')).count();
        return chinese > latin ? "ZH" : latin > chinese ? "EN" : "MIXED";
    }

    private static String formatDuration(long startedNanos) {
        long elapsedMillis = Duration.ofNanos(System.nanoTime() - startedNanos).toMillis();
        return elapsedMillis < 1000
                ? elapsedMillis + " ms"
                : String.format("%.2f s", elapsedMillis / 1000.0);
    }

    private static Response post(String path, JsonNode body) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl() + path))
                .timeout(TIMEOUT).header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(body))).build();
        HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
        return new Response(response.statusCode(), response.body());
    }

    private static String baseUrl() {
        return env("MEMIND_BASE_URL", "http://localhost:8366").replaceAll("/+$", "");
    }

    private static List<JsonNode> nodes(JsonNode node) {
        List<JsonNode> result = new ArrayList<>();
        node.forEach(result::add);
        return result;
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String abbreviate(String value, int limit) {
        String text = value == null ? "" : value.replace('\n', ' ').replace('\r', ' ');
        return text.length() <= limit ? text : text.substring(0, limit) + "...";
    }

    private record Response(int status, String body) {}

    private record QueryEvaluation(String status, List<JsonNode> items, List<JsonNode> rawData) {}

    private record Options(
            String file,
            int maxSessions,
            int maxQa,
            String mode,
            String agentId,
            boolean asyncIngest,
            int waitSeconds,
            String qaId,
            String resumeAfter,
            String ingestPolicy,
            int pollSeconds,
            String progressFile,
            String userId,
            String resultFile) {
        static Options parse(String[] args) {
            String file = null, mode = "all", agentId = "benchmark-v107-quick";
            String qaId = null, resumeAfter = null, ingestPolicy = "timeline", progressFile = null;
            String userId = null, resultFile = null;
            int maxSessions = 3, maxQa = 5, waitSeconds = 180, pollSeconds = 5;
            boolean asyncIngest = false;
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--file" -> file = args[++i];
                    case "--max-sessions" -> maxSessions = Integer.parseInt(args[++i]);
                    case "--max-qa" -> maxQa = Integer.parseInt(args[++i]);
                    case "--mode" -> mode = args[++i];
                    case "--agent-id" -> agentId = args[++i];
                    case "--async-ingest" -> asyncIngest = true;
                    case "--wait-seconds" -> waitSeconds = Integer.parseInt(args[++i]);
                    case "--qa-id" -> qaId = args[++i];
                    case "--resume-after" -> resumeAfter = args[++i];
                    case "--ingest-policy" -> ingestPolicy = args[++i];
                    case "--poll-seconds" -> pollSeconds = Integer.parseInt(args[++i]);
                    case "--progress-file" -> progressFile = args[++i];
                    case "--user-id" -> userId = args[++i];
                    case "--result-file" -> resultFile = args[++i];
                    default -> throw new IllegalArgumentException("Unknown argument: " + args[i]);
                }
            }
            if (file == null || !List.of("ingest", "query", "all").contains(mode)
                    || !List.of("timeline", "evidence-only").contains(ingestPolicy)) {
                throw new IllegalArgumentException(
                        "Usage: --file <json> [--max-sessions N] [--max-qa N] "
                                + "[--mode ingest|query|all] [--async-ingest] [--wait-seconds N] "
                                + "[--qa-id ID] [--resume-after SESSION] "
                                + "[--ingest-policy timeline|evidence-only]");
            }
            if (!Files.isRegularFile(Path.of(file))) {
                throw new IllegalArgumentException("Dataset file not found: " + file);
            }
            if (maxSessions < 0 || maxQa < 0 || waitSeconds < 1 || pollSeconds < 1) {
                throw new IllegalArgumentException(
                        "Limits must not be negative; wait and poll seconds must be positive");
            }
            return new Options(
                    file,
                    maxSessions,
                    maxQa,
                    mode,
                    agentId,
                    asyncIngest,
                    waitSeconds,
                    qaId,
                    resumeAfter,
                    ingestPolicy,
                    pollSeconds,
                    progressFile,
                    userId,
                    resultFile);
        }
    }
}
