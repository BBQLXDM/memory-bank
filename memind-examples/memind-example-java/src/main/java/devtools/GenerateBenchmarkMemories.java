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
package devtools;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.openmemind.ai.client.MemindClient;
import com.openmemind.ai.client.model.common.ContentBlock;
import com.openmemind.ai.client.model.common.Message;
import com.openmemind.ai.client.model.common.Role;
import com.openmemind.ai.client.model.request.AddMessageRequest;
import com.openmemind.ai.client.model.request.CommitMemoryRequest;
import com.openmemind.ai.client.model.request.QueryMemoryItemsRequest;
import com.openmemind.ai.client.model.response.QueryMemoryItemsResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

public final class GenerateBenchmarkMemories {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final String DEFAULT_SERVER =
            System.getenv().getOrDefault("MEMIND_SERVER", "http://127.0.0.1:8366");
    private static final Path DEFAULT_BENCHMARK_DIR =
            Path.of(
                    System.getenv()
                            .getOrDefault(
                                    "BENCHMARK_DIR",
                                    "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"));
    private static final Path DEFAULT_OUT_DIR =
            Path.of(
                    System.getenv()
                            .getOrDefault(
                                    "OUT_DIR",
                                    Path.of(
                                                    System.getProperty("user.dir"),
                                                    "benchmark-results",
                                                    "memory-generation-java")
                                            .toString()));
    private static final String DEFAULT_SOURCE_CLIENT =
            System.getenv().getOrDefault("SOURCE_CLIENT", "benchmark-v107");
    private static final String DEFAULT_ID_PREFIX =
            System.getenv().getOrDefault("ID_PREFIX", "benchmark-v107");
    private static final Path DEFAULT_SEEN_DIR =
            Path.of(
                    System.getenv()
                            .getOrDefault(
                                    "MEMIND_SEEN_DIR",
                                    Path.of(
                                                    System.getProperty("user.dir"),
                                                    "benchmark-results",
                                                    "memory-generation-seen")
                                            .toString()));

    private static final List<CompanySpec> DEFAULT_COMPANIES =
            List.of(
                    new CompanySpec(
                            "C017",
                            "联科绿筑新型建材有限公司",
                            "benchmark-v107-C017",
                            "benchmark-v107-C017-agent",
                            "联科绿筑新型建材有限公司.json"),
                    new CompanySpec(
                            "C018",
                            "鑫源精密机械制造有限公司",
                            "benchmark-v107-C018",
                            "benchmark-v107-C018-agent",
                            "鑫源精密机械制造有限公司.json"),
                    new CompanySpec(
                            "C020",
                            "锐科航空装备股份有限公司",
                            "benchmark-v107-C020",
                            "benchmark-v107-C020-agent",
                            "锐科航空装备股份有限公司.json"));

    private GenerateBenchmarkMemories() {}

    public static void main(String[] args) throws Exception {
        Arguments arguments = Arguments.parse(args);
        String projectId = projectIdFor(arguments.benchmarkDir);

        List<CompanyRuntime> companies =
                DEFAULT_COMPANIES.stream()
                        .filter(
                                spec ->
                                        arguments.companyIds.isEmpty()
                                                || arguments.companyIds.contains(spec.companyId))
                        .map(
                                spec ->
                                        spec.withIds(
                                                arguments.idPrefix + "-" + spec.companyId,
                                                arguments.idPrefix
                                                        + "-"
                                                        + spec.companyId
                                                        + "-agent",
                                                projectId + "-" + spec.companyId))
                        .toList();
        if (companies.isEmpty()) {
            throw new IllegalArgumentException("No matching companies selected");
        }

        Files.createDirectories(arguments.outDir);
        Files.createDirectories(arguments.seenDir);

        System.out.println("Server: " + arguments.server);
        System.out.println("Benchmark dir: " + arguments.benchmarkDir);
        System.out.println("ProjectId: " + projectId);
        System.out.println("Output dir: " + arguments.outDir);
        System.out.println("Seen dir: " + arguments.seenDir);
        System.out.println("Mode: messages");
        System.out.println("Id prefix: " + arguments.idPrefix);
        System.out.println("Source client: " + arguments.sourceClient);
        System.out.println(
                "Companies: "
                        + companies.stream()
                                .map(spec -> spec.companyId)
                                .reduce((a, b) -> a + ", " + b)
                                .orElse(""));
        System.out.println("Dry run: " + arguments.dryRun);
        System.out.println("Session limit: " + arguments.sessionLimit);
        System.out.println(
                "Sessions: "
                        + (arguments.sessionIds.isEmpty()
                                ? "ALL"
                                : String.join(", ", arguments.sessionIds)));
        System.out.println("=".repeat(100));

        List<Map<String, Object>> companyRows = new ArrayList<>();
        List<Map<String, Object>> sessionRows = new ArrayList<>();

        try (MemindClient healthClient =
                createClient(arguments.server, arguments.connectTimeoutSeconds, 30)) {
            System.out.println("Health: " + healthClient.health().status());
        }

        System.out.println(
                "Skip rule: success + sessionId + optional projectId/sourceClient/userId/agentId"
                        + " match");

        for (CompanyRuntime company : companies) {
            Path companyFile = arguments.benchmarkDir.resolve(company.fileName);
            if (!Files.exists(companyFile)) {
                System.out.println("[WARN] missing benchmark file: " + companyFile);
                continue;
            }

            CompanyData companyData = CompanyData.load(companyFile);
            List<SessionData> sessions = new ArrayList<>(companyData.sessions());
            sessions.sort(Comparator.comparing(SessionData::startedAt));
            if (arguments.sessionLimit > 0 && arguments.sessionLimit < sessions.size()) {
                sessions = sessions.subList(0, arguments.sessionLimit);
            }

            Path companyDir = arguments.outDir.resolve(company.companyId);
            Files.createDirectories(companyDir);
            Path seenCompanyDir = arguments.seenDir.resolve(company.companyId);
            Files.createDirectories(seenCompanyDir);

            System.out.println("=".repeat(100));
            System.out.println("Company: " + company.companyId + " " + company.companyName);
            System.out.println("ProjectId: " + company.projectId);
            System.out.println("Sessions: " + sessions.size());

            int successSessions = 0;
            int failedSessions = 0;
            int totalTurns = 0;

            for (SessionData session : sessions) {
                if (!arguments.sessionIds.isEmpty()
                        && !arguments.sessionIds.contains(session.sessionId)) {
                    System.out.println(
                            "[SESSION] " + session.sessionId + " filtered by --sessions, skipped");
                    continue;
                }
                totalTurns += session.turns().size();
                Path requestPath = companyDir.resolve(session.sessionId + ".request.json");
                Path responsePath = companyDir.resolve(session.sessionId + ".response.json");
                Path seenResponsePath =
                        seenCompanyDir.resolve(session.sessionId + ".response.json");
                String sessionSourceClient =
                        arguments.sourceClient + "-" + company.companyId + "-" + session.sessionId;
                if (!arguments.dryRun) {
                    if (hasSuccessfulResponse(
                            seenResponsePath, company, session, sessionSourceClient)) {
                        SessionResult skipped =
                                SessionResult.skipped(
                                        company, session, requestPath, seenResponsePath);
                        sessionRows.add(skipped.toRow());
                        successSessions++;
                        System.out.println(
                                "[SESSION] "
                                        + session.sessionId
                                        + " skipped existing success seen="
                                        + seenResponsePath);
                        continue;
                    }
                    try (MemindClient probeClient =
                            createClient(arguments.server, arguments.connectTimeoutSeconds, 30)) {
                        if (hasServerItems(probeClient, company, sessionSourceClient)) {
                            SessionResult skipped =
                                    SessionResult.skipped(
                                            company, session, requestPath, responsePath);
                            sessionRows.add(skipped.toRow());
                            successSessions++;
                            System.out.println(
                                    "[SESSION] "
                                            + session.sessionId
                                            + " skipped, already present on server (sourceClient="
                                            + sessionSourceClient
                                            + ")");
                            continue;
                        }
                    }
                }
                long sessionReadTimeoutSeconds =
                        estimateSessionTimeoutSeconds(session.turns().size());
                SessionResult result =
                        arguments.dryRun
                                ? SessionResult.dryRun(company, session)
                                : runSession(
                                        arguments.server,
                                        arguments.connectTimeoutSeconds,
                                        sessionReadTimeoutSeconds,
                                        company,
                                        session,
                                        companyDir,
                                        seenCompanyDir,
                                        sessionSourceClient);

                sessionRows.add(result.toRow());
                if (result.success) {
                    successSessions++;
                } else {
                    failedSessions++;
                }

                System.out.println(
                        "[SESSION] "
                                + session.sessionId
                                + " timeout="
                                + sessionReadTimeoutSeconds
                                + "s status="
                                + result.statusCode
                                + " turns="
                                + session.turns().size()
                                + " error="
                                + result.error);
            }

            companyRows.add(
                    Map.of(
                            "company_id",
                            company.companyId,
                            "company_name",
                            company.companyName,
                            "sessions",
                            sessions.size(),
                            "turns",
                            totalTurns,
                            "success_sessions",
                            successSessions,
                            "failed_sessions",
                            failedSessions));
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("server", arguments.server);
        summary.put("benchmark_dir", arguments.benchmarkDir.toString());
        summary.put("project_id", projectId);
        summary.put("out_dir", arguments.outDir.toString());
        summary.put("mode", "messages");
        summary.put("source_client", arguments.sourceClient);
        summary.put("dry_run", arguments.dryRun);
        summary.put("companies", companyRows);
        writeJson(arguments.outDir.resolve("summary.json"), summary);
        writeCsv(arguments.outDir.resolve("summary.csv"), sessionRows);

        System.out.println("=".repeat(100));
        System.out.println("Done. Summary written to: " + arguments.outDir.resolve("summary.json"));
        System.out.println("CSV written to: " + arguments.outDir.resolve("summary.csv"));
    }

    private static SessionResult runSession(
            String server,
            int connectTimeoutSeconds,
            long readTimeoutSeconds,
            CompanyRuntime company,
            SessionData session,
            Path companyDir,
            Path seenCompanyDir,
            String sourceClient)
            throws IOException {
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("mode", "messages");
        requestBody.put("project_id", company.projectId);
        requestBody.put("session_id", session.sessionId);
        requestBody.put("turns", session.turns().size());
        requestBody.put("source_client", sourceClient);

        Path requestPath = companyDir.resolve(session.sessionId + ".request.json");
        Path seenResponsePath = seenCompanyDir.resolve(session.sessionId + ".response.json");
        writeJson(requestPath, requestBody);

        try (MemindClient client =
                createClient(server, connectTimeoutSeconds, readTimeoutSeconds)) {
            for (TurnData turn : session.turns()) {
                Message message =
                        toMessage(turn, company.projectId, session.sessionId, sourceClient);
                client.addMessage(
                        AddMessageRequest.builder()
                                .userId(company.userId)
                                .agentId(company.agentId)
                                .message(message)
                                .sourceClient(sourceClient)
                                .build());
            }

            client.commit(
                    CommitMemoryRequest.builder()
                            .userId(company.userId)
                            .agentId(company.agentId)
                            .sourceClient(sourceClient)
                            .build());

            Map<String, Object> response = new LinkedHashMap<>();
            response.put("status", "success");
            response.put("userId", company.userId);
            response.put("agentId", company.agentId);
            response.put("projectId", company.projectId);
            response.put("sessionId", session.sessionId);
            response.put("sourceClient", sourceClient);
            writeJson(seenResponsePath, response);
            return SessionResult.success(company, session, requestPath, seenResponsePath);
        } catch (RuntimeException ex) {
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("error", ex.getMessage());
            response.put("exception", ex.getClass().getName());
            response.put("sessionId", session.sessionId);
            response.put("sourceClient", sourceClient);
            writeJson(seenResponsePath, response);
            return SessionResult.failure(
                    company, session, requestPath, seenResponsePath, ex.getMessage());
        }
    }

    private static MemindClient createClient(
            String server, int connectTimeoutSeconds, long readTimeoutSeconds) {
        return MemindClient.builder()
                .baseUrl(server)
                .connectTimeout(Duration.ofSeconds(connectTimeoutSeconds))
                .readTimeout(Duration.ofSeconds(readTimeoutSeconds))
                .build();
    }

    private static boolean hasSuccessfulResponse(
            Path responsePath, CompanyRuntime company, SessionData session, String sourceClient) {
        if (!Files.exists(responsePath)) {
            return false;
        }
        try {
            JsonNode root =
                    OBJECT_MAPPER.readTree(Files.readString(responsePath, StandardCharsets.UTF_8));
            if (!"success".equalsIgnoreCase(root.path("status").asText())) {
                return false;
            }
            if (!session.sessionId.equals(root.path("sessionId").asText())) {
                return false;
            }

            String existingProjectId = root.path("projectId").asText("").trim();
            if (!existingProjectId.isEmpty() && !company.projectId.equals(existingProjectId)) {
                return false;
            }

            String existingSourceClient = root.path("sourceClient").asText("").trim();
            if (!existingSourceClient.isEmpty() && !sourceClient.equals(existingSourceClient)) {
                return false;
            }

            String existingUserId = root.path("userId").asText("").trim();
            if (!existingUserId.isEmpty() && !company.userId.equals(existingUserId)) {
                return false;
            }

            String existingAgentId = root.path("agentId").asText("").trim();
            if (!existingAgentId.isEmpty() && !company.agentId.equals(existingAgentId)) {
                return false;
            }

            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    private static Message toMessage(
            TurnData turn, String projectId, String sessionId, String sourceClient) {
        Role role = "assistant".equalsIgnoreCase(turn.role()) ? Role.ASSISTANT : Role.USER;
        String text = "[project=" + projectId + "][session=" + sessionId + "] " + turn.content();
        return new Message(
                role,
                List.of(new ContentBlock.TextBlock(text)),
                turn.timestamp(),
                null,
                sourceClient);
    }

    private static boolean hasServerItems(
            MemindClient client, CompanyRuntime company, String sourceClient) {
        try {
            QueryMemoryItemsResponse response =
                    client.queryItems(
                            QueryMemoryItemsRequest.builder()
                                    .userId(company.userId)
                                    .agentId(company.agentId)
                                    .sourceClients(List.of(sourceClient))
                                    .limit(1)
                                    .build());
            return response != null && response.items() != null && !response.items().isEmpty();
        } catch (RuntimeException ex) {
            System.out.println(
                    "[WARN] server probe query failed for sourceClient="
                            + sourceClient
                            + ": "
                            + ex.getMessage());
            return false;
        }
    }

    private static long estimateSessionTimeoutSeconds(int turns) {
        long estimate = 180L + (long) turns * 30L;
        return Math.max(180L, Math.min(estimate, 600L));
    }

    private static String projectIdFor(Path benchmarkDir) {
        Path normalized = benchmarkDir.toAbsolutePath().normalize();
        String input = normalized.toString();
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-1");
            byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            return normalized.getFileName() + "-" + HexFormat.of().formatHex(hash, 0, 6);
        } catch (Exception e) {
            return normalized.getFileName() + "-" + Integer.toHexString(input.hashCode());
        }
    }

    private static void writeJson(Path path, Object value) throws IOException {
        Files.writeString(
                path,
                OBJECT_MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(value)
                        + System.lineSeparator(),
                StandardCharsets.UTF_8);
    }

    private static void writeCsv(Path path, List<Map<String, Object>> rows) throws IOException {
        StringBuilder sb = new StringBuilder();
        sb.append(
                "company_id,company_name,session_id,status_code,success,error,request_path,response_path\n");
        for (Map<String, Object> row : rows) {
            sb.append(csv(row.get("company_id")))
                    .append(',')
                    .append(csv(row.get("company_name")))
                    .append(',')
                    .append(csv(row.get("session_id")))
                    .append(',')
                    .append(csv(row.get("status_code")))
                    .append(',')
                    .append(csv(row.get("success")))
                    .append(',')
                    .append(csv(row.get("error")))
                    .append(',')
                    .append(csv(row.get("request_path")))
                    .append(',')
                    .append(csv(row.get("response_path")))
                    .append('\n');
        }
        Files.writeString(path, sb.toString(), StandardCharsets.UTF_8);
    }

    private static String csv(Object value) {
        String text = Objects.toString(value, "");
        return '"' + text.replace("\"", "\"\"") + '"';
    }

    private static Instant parseInstant(Object value) {
        if (value == null) {
            return Instant.EPOCH;
        }
        String text = Objects.toString(value, "").trim();
        if (text.isEmpty()) {
            return Instant.EPOCH;
        }
        try {
            return Instant.parse(text);
        } catch (Exception ignored) {
            try {
                return OffsetDateTime.parse(text).toInstant();
            } catch (Exception secondIgnored) {
                return Instant.EPOCH;
            }
        }
    }

    private static final class CompanyData {
        private final List<SessionData> sessions;

        private CompanyData(List<SessionData> sessions) {
            this.sessions = sessions;
        }

        List<SessionData> sessions() {
            return sessions;
        }

        static CompanyData load(Path path) throws IOException {
            JsonNode root = OBJECT_MAPPER.readTree(Files.readString(path, StandardCharsets.UTF_8));
            JsonNode context = root.path("context");
            JsonNode sessionsNode = context.path("sessions");
            List<SessionData> sessions = new ArrayList<>();
            if (sessionsNode.isArray()) {
                for (JsonNode sessionNode : sessionsNode) {
                    String sessionId = sessionNode.path("session_id").asText("unknown-session");
                    Instant startedAt =
                            parseInstant(
                                    sessionNode.path("started_at").isMissingNode()
                                            ? null
                                            : sessionNode.path("started_at").asText());
                    List<TurnData> turns = new ArrayList<>();
                    JsonNode turnsNode = sessionNode.path("turns");
                    if (turnsNode.isArray()) {
                        for (JsonNode turnNode : turnsNode) {
                            String role = turnNode.path("role").asText("user");
                            String content = turnNode.path("content").asText("");
                            Instant timestamp =
                                    parseInstant(
                                            turnNode.path("timestamp").isMissingNode()
                                                    ? null
                                                    : turnNode.path("timestamp").asText());
                            turns.add(new TurnData(role, content, timestamp));
                        }
                    }
                    sessions.add(new SessionData(sessionId, startedAt, turns));
                }
            }
            return new CompanyData(sessions);
        }
    }

    private record CompanySpec(
            String companyId, String companyName, String userId, String agentId, String fileName) {
        CompanyRuntime withIds(String userId, String agentId, String projectId) {
            return new CompanyRuntime(companyId, companyName, userId, agentId, fileName, projectId);
        }
    }

    private record CompanyRuntime(
            String companyId,
            String companyName,
            String userId,
            String agentId,
            String fileName,
            String projectId) {}

    private record SessionData(String sessionId, Instant startedAt, List<TurnData> turns) {}

    private record TurnData(String role, String content, Instant timestamp) {}

    private record SessionResult(
            String companyId,
            String companyName,
            String sessionId,
            int statusCode,
            boolean success,
            String error,
            Path requestPath,
            Path responsePath) {

        static SessionResult success(
                CompanyRuntime company, SessionData session, Path requestPath, Path responsePath) {
            return new SessionResult(
                    company.companyId,
                    company.companyName,
                    session.sessionId,
                    200,
                    true,
                    null,
                    requestPath,
                    responsePath);
        }

        static SessionResult failure(
                CompanyRuntime company,
                SessionData session,
                Path requestPath,
                Path responsePath,
                String error) {
            return new SessionResult(
                    company.companyId,
                    company.companyName,
                    session.sessionId,
                    400,
                    false,
                    error,
                    requestPath,
                    responsePath);
        }

        static SessionResult dryRun(CompanyRuntime company, SessionData session) {
            return new SessionResult(
                    company.companyId,
                    company.companyName,
                    session.sessionId,
                    0,
                    true,
                    "dry_run",
                    null,
                    null);
        }

        static SessionResult skipped(
                CompanyRuntime company, SessionData session, Path requestPath, Path responsePath) {
            return new SessionResult(
                    company.companyId,
                    company.companyName,
                    session.sessionId,
                    208,
                    true,
                    "skipped_existing_success",
                    requestPath,
                    responsePath);
        }

        Map<String, Object> toRow() {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("company_id", companyId);
            row.put("company_name", companyName);
            row.put("session_id", sessionId);
            row.put("status_code", statusCode);
            row.put("success", success);
            row.put("error", error);
            row.put("request_path", requestPath == null ? "" : requestPath.toString());
            row.put("response_path", responsePath == null ? "" : responsePath.toString());
            return row;
        }
    }

    private record Arguments(
            String server,
            Path benchmarkDir,
            Path outDir,
            Path seenDir,
            String sourceClient,
            String idPrefix,
            boolean dryRun,
            int sessionLimit,
            int connectTimeoutSeconds,
            int readTimeoutSeconds,
            List<String> companyIds,
            List<String> sessionIds) {
        static Arguments parse(String[] args) {
            String server = DEFAULT_SERVER;
            Path benchmarkDir = DEFAULT_BENCHMARK_DIR;
            Path outDir = DEFAULT_OUT_DIR;
            Path seenDir =
                    Path.of(
                            System.getenv()
                                    .getOrDefault(
                                            "SEEN_DIR",
                                            Path.of(
                                                            System.getProperty("user.dir"),
                                                            "benchmark-results",
                                                            "memory-generation-seen")
                                                    .toString()));
            String sourceClient = DEFAULT_SOURCE_CLIENT;
            String idPrefix = DEFAULT_ID_PREFIX;
            boolean dryRun = false;
            int sessionLimit = 0;
            int connectTimeoutSeconds = 5;
            int readTimeoutSeconds = 30;
            List<String> companyIds = new ArrayList<>();
            List<String> sessionIds = new ArrayList<>();

            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--server" -> server = args[++i];
                    case "--benchmark-dir" -> benchmarkDir = Path.of(args[++i]);
                    case "--out-dir" -> outDir = Path.of(args[++i]);
                    case "--seen-dir" -> seenDir = Path.of(args[++i]);
                    case "--source-client" -> sourceClient = args[++i];
                    case "--id-prefix" -> idPrefix = args[++i];
                    case "--dry-run" -> dryRun = true;
                    case "--session-limit" -> sessionLimit = Integer.parseInt(args[++i]);
                    case "--connect-timeout-seconds" ->
                            connectTimeoutSeconds = Integer.parseInt(args[++i]);
                    case "--read-timeout-seconds" ->
                            readTimeoutSeconds = Integer.parseInt(args[++i]);
                    case "--companies" -> {
                        while (i + 1 < args.length && !args[i + 1].startsWith("--")) {
                            companyIds.add(args[++i].toUpperCase(Locale.ROOT));
                        }
                    }
                    case "--sessions" -> {
                        while (i + 1 < args.length && !args[i + 1].startsWith("--")) {
                            sessionIds.add(args[++i].toUpperCase(Locale.ROOT));
                        }
                    }
                    default -> throw new IllegalArgumentException("Unknown arg: " + args[i]);
                }
            }

            return new Arguments(
                    server,
                    benchmarkDir,
                    outDir,
                    seenDir,
                    sourceClient,
                    idPrefix,
                    dryRun,
                    sessionLimit,
                    connectTimeoutSeconds,
                    readTimeoutSeconds,
                    companyIds,
                    sessionIds);
        }
    }
}
