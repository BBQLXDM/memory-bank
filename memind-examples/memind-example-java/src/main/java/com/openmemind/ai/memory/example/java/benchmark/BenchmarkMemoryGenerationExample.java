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
package com.openmemind.ai.memory.example.java.benchmark;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.openmemind.ai.client.MemindClient;
import com.openmemind.ai.client.model.common.ContentBlock;
import com.openmemind.ai.client.model.common.Message;
import com.openmemind.ai.client.model.common.Role;
import com.openmemind.ai.client.model.request.AddMessageRequest;
import com.openmemind.ai.client.model.request.CommitMemoryRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

public final class BenchmarkMemoryGenerationExample {

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

    private static final List<CompanySpec> DEFAULT_COMPANIES =
            List.of(
                    new CompanySpec(
                            "C017",
                            "联科绿筑新型建材有限公司",
                            "benchmark-v107-C017-isolated",
                            "benchmark-v107-C017-isolated-agent",
                            "联科绿筑新型建材有限公司.json"),
                    new CompanySpec(
                            "C018",
                            "鑫源精密机械制造有限公司",
                            "benchmark-v107-C018-isolated",
                            "benchmark-v107-C018-isolated-agent",
                            "鑫源精密机械制造有限公司.json"),
                    new CompanySpec(
                            "C020",
                            "锐科航空装备股份有限公司",
                            "benchmark-v107-C020-isolated",
                            "benchmark-v107-C020-isolated-agent",
                            "锐科航空装备股份有限公司.json"));

    private BenchmarkMemoryGenerationExample() {}

    public static void main(String[] args) throws Exception {
        Arguments arguments = Arguments.parse(args);
        List<CompanySpec> companies =
                DEFAULT_COMPANIES.stream()
                        .filter(
                                spec ->
                                        arguments.companyIds.isEmpty()
                                                || arguments.companyIds.contains(spec.companyId))
                        .toList();
        if (companies.isEmpty()) {
            throw new IllegalArgumentException("No matching companies selected");
        }

        Files.createDirectories(arguments.outDir);

        System.out.println("Server: " + arguments.server);
        System.out.println("Benchmark dir: " + arguments.benchmarkDir);
        System.out.println("Output dir: " + arguments.outDir);
        System.out.println("Mode: messages");
        System.out.println("Source client: " + arguments.sourceClient);
        System.out.println(
                "Companies: "
                        + companies.stream()
                                .map(spec -> spec.companyId)
                                .reduce((a, b) -> a + ", " + b)
                                .orElse(""));
        System.out.println("Dry run: " + arguments.dryRun);
        System.out.println("Session limit: " + arguments.sessionLimit);
        System.out.println("=".repeat(100));

        List<Map<String, Object>> companyRows = new ArrayList<>();
        List<Map<String, Object>> sessionRows = new ArrayList<>();

        try (MemindClient client = MemindClient.builder().baseUrl(arguments.server).build()) {
            System.out.println("Health: " + client.health().status());

            for (CompanySpec company : companies) {
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

                System.out.println("=".repeat(100));
                System.out.println("Company: " + company.companyId + " " + company.companyName);
                System.out.println("Sessions: " + sessions.size());

                int successSessions = 0;
                int failedSessions = 0;
                int totalTurns = 0;

                for (SessionData session : sessions) {
                    totalTurns += session.turns().size();
                    SessionResult result =
                            arguments.dryRun
                                    ? SessionResult.dryRun(company, session)
                                    : runSession(
                                            client,
                                            company,
                                            session,
                                            companyDir,
                                            arguments.sourceClient);

                    sessionRows.add(result.toRow());
                    if (result.success) {
                        successSessions++;
                    } else {
                        failedSessions++;
                    }

                    System.out.println(
                            "[SESSION] "
                                    + session.sessionId
                                    + " status="
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
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("server", arguments.server);
        summary.put("benchmark_dir", arguments.benchmarkDir.toString());
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
            MemindClient client,
            CompanySpec company,
            SessionData session,
            Path companyDir,
            String sourceClient)
            throws IOException {
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("mode", "messages");
        requestBody.put("session_id", session.sessionId);
        requestBody.put("turns", session.turns().size());

        Path requestPath = companyDir.resolve(session.sessionId + ".request.json");
        Path responsePath = companyDir.resolve(session.sessionId + ".response.json");
        writeJson(requestPath, requestBody);

        try {
            for (TurnData turn : session.turns()) {
                Message message = toMessage(turn, sourceClient);
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
            response.put("sessionId", session.sessionId);
            writeJson(responsePath, response);
            return SessionResult.success(company, session, requestPath, responsePath);
        } catch (RuntimeException ex) {
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("error", ex.getMessage());
            response.put("exception", ex.getClass().getName());
            writeJson(responsePath, response);
            return SessionResult.failure(
                    company, session, requestPath, responsePath, ex.getMessage());
        }
    }

    private static Message toMessage(TurnData turn, String sourceClient) {
        Role role = "assistant".equalsIgnoreCase(turn.role()) ? Role.ASSISTANT : Role.USER;
        List<ContentBlock> content = List.of(new ContentBlock.TextBlock(turn.content()));
        return new Message(role, content, turn.timestamp(), null, sourceClient);
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
            String companyId, String companyName, String userId, String agentId, String fileName) {}

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
                CompanySpec company, SessionData session, Path requestPath, Path responsePath) {
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
                CompanySpec company,
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

        static SessionResult dryRun(CompanySpec company, SessionData session) {
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
            String sourceClient,
            boolean dryRun,
            int sessionLimit,
            List<String> companyIds) {
        static Arguments parse(String[] args) {
            String server = DEFAULT_SERVER;
            Path benchmarkDir = DEFAULT_BENCHMARK_DIR;
            Path outDir = DEFAULT_OUT_DIR;
            String sourceClient = DEFAULT_SOURCE_CLIENT;
            boolean dryRun = false;
            int sessionLimit = 0;
            List<String> companyIds = new ArrayList<>();

            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--server" -> server = args[++i];
                    case "--benchmark-dir" -> benchmarkDir = Path.of(args[++i]);
                    case "--out-dir" -> outDir = Path.of(args[++i]);
                    case "--source-client" -> sourceClient = args[++i];
                    case "--dry-run" -> dryRun = true;
                    case "--session-limit" -> sessionLimit = Integer.parseInt(args[++i]);
                    case "--companies" -> {
                        while (i + 1 < args.length && !args[i + 1].startsWith("--")) {
                            companyIds.add(args[++i].toUpperCase(Locale.ROOT));
                        }
                    }
                    default -> throw new IllegalArgumentException("Unknown arg: " + args[i]);
                }
            }

            return new Arguments(
                    server, benchmarkDir, outDir, sourceClient, dryRun, sessionLimit, companyIds);
        }
    }
}
