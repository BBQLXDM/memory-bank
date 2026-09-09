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
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Focused regression verifier for the C019 evaluation set.
 *
 * <p>It reruns the selected questions against the live Memind service using both SIMPLE and DEEP
 * retrieval, then writes a machine-readable summary so you can compare whether the broader retrieval
 * changes actually helped.
 *
 * <p>Default input:
 * <pre>
 * benchmark-results/full-pipeline-java/C019-full-results.jsonl
 * </pre>
 *
 * <p>Default output:
 * <pre>
 * benchmark-results/regression-c019/c019-regression-results.jsonl
 * </pre>
 */
public final class C019RegressionVerifier {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(180);

    private static final Set<String> FOCUS_QA_IDS = Set.of(
            "std_鑫科精密_t0035",
            "std_鑫科精密_t0056",
            "std_鑫科精密_t0062",
            "std_鑫科精密_t0075",
            "std_鑫科精密_t0083",
            "std_鑫科精密_t0095",
            "std_鑫科精密_t0111",
            "std_鑫科精密_t0150",
            "std_鑫科精密_t0268",
            "std_鑫科精密_t0275",
            "std_鑫科精密_u0283",
            "std_鑫科精密_u0284");

    private static final Set<String> HISTORY_PRIORITY_QA_IDS = Set.of(
            "std_鑫科精密_t0150",
            "std_鑫科精密_t0268",
            "std_鑫科精密_t0275",
            "std_鑫科精密_u0283",
            "std_鑫科精密_u0284");

    private static final Set<String> CAUSAL_SUMMARY_QA_IDS = Set.of(
            "std_鑫科精密_t0075",
            "std_鑫科精密_t0150",
            "std_鑫科精密_t0268",
            "std_鑫科精密_t0275");

    private static final Map<String, List<String>> NEEDLES = Map.ofEntries(
            Map.entry("std_鑫科精密_t0035", List.of("工行账户", "ICBC account", "all fund transactions")),
            Map.entry("std_鑫科精密_t0056", List.of("无支用限制", "no restriction", "无异常情况")),
            Map.entry("std_鑫科精密_t0062", List.of("精密齿轮", "轴承", "冲压件", "注塑件")),
            Map.entry("std_鑫科精密_t0075", List.of("配套设施完善", "仓储区", "生产车间", "研发楼", "厂房")),
            Map.entry("std_鑫科精密_t0083", List.of("QC-ZZ-2025091001")),
            Map.entry("std_鑫科精密_t0095", List.of("84万元", "840,000", "840000", "90天以上逾期")),
            Map.entry("std_鑫科精密_t0111", List.of("合格", "无整改项", "无违规经营行为", "联合检查")),
            Map.entry("std_鑫科精密_t0150", List.of("每季度", "每月", "每半年", "贷后检查")),
            Map.entry("std_鑫科精密_t0268", List.of("授信额度", "影响", "贬值", "抵押物")),
            Map.entry("std_鑫科精密_t0275", List.of("禁止", "拍照", "录音", "精密加工区域")),
            Map.entry("std_鑫科精密_u0283", List.of("LPR下浮30个基点", "30个基点", "更正前")),
            Map.entry("std_鑫科精密_u0284", List.of("LPR下浮50个基点", "50个基点", "更正后")));

    private C019RegressionVerifier() {}

    public static void main(String[] args) throws Exception {
        Options options = Options.parse(args);
        Files.createDirectories(options.outputDir);

        List<JsonNode> records = readJsonl(options.input);
        List<JsonNode> targets = new ArrayList<>();
        for (JsonNode record : records) {
            if (FOCUS_QA_IDS.contains(record.path("qaId").asText())) {
                targets.add(record);
            }
        }
        if (targets.isEmpty()) {
            throw new IllegalStateException("No focus questions found in: " + options.input);
        }

        List<ObjectNode> results = new ArrayList<>();
        System.out.println("=== Focused regression start ===");
        System.out.println("Input:  " + options.input);
        System.out.println("Output: " + options.outputFile);
        System.out.println("Server: " + options.serverBaseUrl);
        System.out.println("Questions: " + targets.size());

        for (JsonNode record : targets) {
            String qaId = record.path("qaId").asText();
            String question = record.path("question").asText();
            String userId = record.path("userId").asText();
            String agentId = record.path("agentId").asText();
            String expected = record.path("expectedAnswer").asText();
            String historyQuery = buildHistoryPriorityQuery(record, question);

            for (String strategy : options.strategies) {
                try {
                    ObjectNode response = retrieve(options.serverBaseUrl, userId, agentId, question, strategy);
                    ObjectNode historyResponse = null;
                    if (HISTORY_PRIORITY_QA_IDS.contains(qaId)) {
                        historyResponse = retrieve(options.serverBaseUrl, userId, agentId, historyQuery, strategy);
                    }
                    ObjectNode causalResponse = null;
                    if (CAUSAL_SUMMARY_QA_IDS.contains(qaId)) {
                        String causalQuery = buildCausalSummaryQuery(record, question);
                        causalResponse = retrieve(options.serverBaseUrl, userId, agentId, causalQuery, strategy);
                    }
                    ObjectNode companyFilteredResponse = null;
                    if (requiresCompanyBoundaryCheck(qaId)) {
                        String companyBoundQuery = buildCompanyBoundaryQuery(record, question);
                        companyFilteredResponse = retrieve(options.serverBaseUrl, userId, agentId, companyBoundQuery, strategy);
                    }
                    List<String> texts = extractTexts(response);
                    List<String> hits = matchedNeedles(qaId, texts);
                    ObjectNode row = JSON.createObjectNode();
                    row.put("qaId", qaId);
                    row.put("question", question);
                    row.put("strategy", strategy);
                    row.put("expectedAnswer", expected);
                    row.put("server", options.serverBaseUrl);
                    row.put("testedAt", Instant.now().toString());
                    row.set("response", response);
                    row.put("itemCount", response.path("data").path("items").size());
                    row.put("rawDataCount", response.path("data").path("rawData").size());
                    row.put("evidenceCount", response.path("data").path("evidences").size());
                    row.set("matchedNeedles", toArray(hits));
                    row.put("top1", firstItemText(response));
                    row.put("historyQueryUsed", false);
                    if (historyResponse != null) {
                        List<String> historyTexts = extractTexts(historyResponse);
                        List<String> historyHits = matchedNeedles(qaId, historyTexts);
                        row.set("historyResponse", historyResponse);
                        row.put("historyQueryUsed", true);
                        row.put("historyItemCount", itemCount(historyResponse));
                        row.put("historyRawDataCount", rawDataCount(historyResponse));
                        row.put("historyEvidenceCount", evidenceCount(historyResponse));
                        row.set("historyMatchedNeedles", toArray(historyHits));
                        row.put("historyTop1", firstItemText(historyResponse));
                    }
                    if (causalResponse != null) {
                        List<String> causalTexts = extractTexts(causalResponse);
                        List<String> causalHits = matchedNeedles(qaId, causalTexts);
                        row.set("causalResponse", causalResponse);
                        row.put("causalQueryUsed", true);
                        row.put("causalItemCount", itemCount(causalResponse));
                        row.put("causalRawDataCount", rawDataCount(causalResponse));
                        row.put("causalEvidenceCount", evidenceCount(causalResponse));
                        row.set("causalMatchedNeedles", toArray(causalHits));
                        row.put("causalTop1", firstItemText(causalResponse));
                    } else {
                        row.put("causalQueryUsed", false);
                    }
                    if (companyFilteredResponse != null) {
                        List<String> companyTexts = extractTexts(companyFilteredResponse);
                        List<String> companyHits = matchedNeedles(qaId, companyTexts);
                        row.set("companyResponse", companyFilteredResponse);
                        row.put("companyQueryUsed", true);
                        row.put("companyItemCount", itemCount(companyFilteredResponse));
                        row.put("companyRawDataCount", rawDataCount(companyFilteredResponse));
                        row.put("companyEvidenceCount", evidenceCount(companyFilteredResponse));
                        row.set("companyMatchedNeedles", toArray(companyHits));
                        row.put("companyTop1", firstItemText(companyFilteredResponse));
                    } else {
                        row.put("companyQueryUsed", false);
                    }
                    results.add(row);

                    String historySummary = "";
                    if (historyResponse != null) {
                        historySummary = String.format(
                                Locale.ROOT,
                                " | history items=%d rawData=%d evidences=%d hits=%s",
                                row.path("historyItemCount").asInt(),
                                row.path("historyRawDataCount").asInt(),
                                row.path("historyEvidenceCount").asInt(),
                                row.path("historyMatchedNeedles").size() == 0
                                        ? "-"
                                        : String.join(",", asStringList(row.path("historyMatchedNeedles"))));
                    }
                    String causalSummary = "";
                    if (causalResponse != null) {
                        causalSummary = String.format(
                                Locale.ROOT,
                                " | causal items=%d rawData=%d evidences=%d hits=%s",
                                row.path("causalItemCount").asInt(),
                                row.path("causalRawDataCount").asInt(),
                                row.path("causalEvidenceCount").asInt(),
                                row.path("causalMatchedNeedles").size() == 0
                                        ? "-"
                                        : String.join(",", asStringList(row.path("causalMatchedNeedles"))));
                    }
                    String companySummary = "";
                    if (companyFilteredResponse != null) {
                        companySummary = String.format(
                                Locale.ROOT,
                                " | company items=%d rawData=%d evidences=%d hits=%s",
                                row.path("companyItemCount").asInt(),
                                row.path("companyRawDataCount").asInt(),
                                row.path("companyEvidenceCount").asInt(),
                                row.path("companyMatchedNeedles").size() == 0
                                        ? "-"
                                        : String.join(",", asStringList(row.path("companyMatchedNeedles"))));
                    }
                    System.out.printf(Locale.ROOT,
                            "%s | %s | items=%d rawData=%d evidences=%d | hits=%s%s%s%s%n",
                            qaId,
                            strategy,
                            row.path("itemCount").asInt(),
                            row.path("rawDataCount").asInt(),
                            row.path("evidenceCount").asInt(),
                            hits.isEmpty() ? "-" : String.join(",", hits),
                            historySummary,
                            causalSummary,
                            companySummary);
                } catch (Exception e) {
                    System.err.printf(Locale.ROOT,
                            "%s | %s | failed: %s%n",
                            qaId,
                            strategy,
                            e.getMessage());
                }
            }
        }

        writeJsonl(options.outputFile, results);
        printSummary(results);
        System.out.println("=== Focused regression done ===");
        System.out.println("Saved: " + options.outputFile);
    }

    private static ObjectNode retrieve(
            String serverBaseUrl,
            String userId,
            String agentId,
            String question,
            String strategy) throws Exception {
        ObjectNode body = JSON.createObjectNode();
        body.put("userId", userId);
        body.put("agentId", agentId);
        body.put("query", question);
        body.put("strategy", strategy);
        body.put("trace", false);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(joinUrl(serverBaseUrl, "/open/v1/memory/retrieve")))
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(body), StandardCharsets.UTF_8))
                .build();

        HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        ObjectNode root = JSON.createObjectNode();
        root.put("httpStatus", response.statusCode());
        root.put("body", response.body());
        if (response.statusCode() >= 200 && response.statusCode() < 300) {
            try {
                root.set("data", JSON.readTree(response.body()));
            } catch (Exception parseError) {
                root.put("parseError", parseError.getMessage());
            }
        }
        return root;
    }

    private static List<JsonNode> readJsonl(Path path) throws IOException {
        List<JsonNode> rows = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty()) {
                rows.add(JSON.readTree(trimmed));
            }
        }
        return rows;
    }

    private static void writeJsonl(Path path, List<ObjectNode> records) throws IOException {
        Files.createDirectories(path.getParent());
        StringBuilder out = new StringBuilder();
        for (ObjectNode record : records) {
            out.append(JSON.writeValueAsString(record)).append(System.lineSeparator());
        }
        Files.writeString(path, out.toString(), StandardCharsets.UTF_8);
    }

    private static List<String> extractTexts(ObjectNode response) {
        List<String> texts = new ArrayList<>();
        JsonNode data = response.path("data").path("data");
        if (data.isMissingNode()) {
            data = response.path("data");
        }
        for (JsonNode item : asArray(data.path("items"))) {
            texts.add(item.path("text").asText(""));
        }
        for (JsonNode raw : asArray(data.path("rawData"))) {
            if (raw.hasNonNull("caption")) {
                texts.add(raw.path("caption").asText(""));
            }
            if (raw.hasNonNull("text")) {
                texts.add(raw.path("text").asText(""));
            }
        }
        for (JsonNode evidence : asArray(data.path("evidences"))) {
            texts.add(evidence.asText(""));
        }
        return texts;
    }

    private static List<String> matchedNeedles(String qaId, List<String> texts) {
        String haystack = String.join("\n", texts);
        List<String> hits = new ArrayList<>();
        for (String needle : NEEDLES.getOrDefault(qaId, List.of())) {
            if (haystack.contains(needle)) {
                hits.add(needle);
            }
        }
        return hits;
    }

    private static String firstItemText(ObjectNode response) {
        JsonNode data = response.path("data").path("data");
        if (data.isMissingNode()) {
            data = response.path("data");
        }
        JsonNode items = data.path("items");
        if (items.isArray() && !items.isEmpty()) {
            return items.get(0).path("text").asText("");
        }
        return "";
    }

    private static int itemCount(ObjectNode response) {
        JsonNode data = response.path("data").path("data");
        if (data.isMissingNode()) {
            data = response.path("data");
        }
        return data.path("items").size();
    }

    private static int rawDataCount(ObjectNode response) {
        JsonNode data = response.path("data").path("data");
        if (data.isMissingNode()) {
            data = response.path("data");
        }
        return data.path("rawData").size();
    }

    private static int evidenceCount(ObjectNode response) {
        JsonNode data = response.path("data").path("data");
        if (data.isMissingNode()) {
            data = response.path("data");
        }
        return data.path("evidences").size();
    }

    private static ArrayNode toArray(List<String> values) {
        ArrayNode array = JSON.createArrayNode();
        for (String value : values) {
            array.add(value);
        }
        return array;
    }

    private static List<JsonNode> asArray(JsonNode node) {
        List<JsonNode> rows = new ArrayList<>();
        if (node != null && node.isArray()) {
            for (JsonNode value : node) {
                rows.add(value);
            }
        }
        return rows;
    }

    private static List<String> asStringList(JsonNode node) {
        List<String> rows = new ArrayList<>();
        if (node != null && node.isArray()) {
            for (JsonNode value : node) {
                rows.add(value.asText(""));
            }
        }
        return rows;
    }

    private static String buildHistoryPriorityQuery(JsonNode record, String question) {
        String qaId = record.path("qaId").asText();
        if (qaId.equals("std_鑫科精密_u0283")) {
            return "更正之前，研发贷利率是多少？请优先使用历史对话中的最早确认结果，不要用当前值。";
        }
        if (qaId.equals("std_鑫科精密_u0284")) {
            return "更正之后，研发贷当前利率是多少？请优先使用历史对话中的最终确认结果，不要用利息金额或优惠描述。";
        }
        return question;
    }

    private static String buildCausalSummaryQuery(JsonNode record, String question) {
        String qaId = record.path("qaId").asText();
        if (qaId.equals("std_鑫科精密_t0268")) {
            return "抵押物复评贬值对授信额度有何影响？请优先返回因果结论，不要只返回抵押物或授信额度数值。";
        }
        if (qaId.equals("std_鑫科精密_t0150")) {
            return "贷后检查周期是如何安排的？请优先返回制度总结，不要只返回单次检查事实。";
        }
        if (qaId.equals("std_鑫科精密_t0075")) {
            return "配套设施完善情况怎样？请优先返回总结性结论，不要只返回厂房、仓储区、研发楼等碎片事实。";
        }
        if (qaId.equals("std_鑫科精密_t0275")) {
            return "生产车间精密加工区域是否允许现场拍照或录音？请优先返回规则结论。";
        }
        return question;
    }

    private static String buildCompanyBoundaryQuery(JsonNode record, String question) {
        String qaId = record.path("qaId").asText();
        if (qaId.startsWith("std_鑫科精密_")) {
            return "请仅基于鑫科精密的历史对话回答：" + question;
        }
        return question;
    }

    private static boolean requiresCompanyBoundaryCheck(String qaId) {
        return qaId.startsWith("std_鑫科精密_");
    }

    private static String joinUrl(String baseUrl, String path) {
        return baseUrl.replaceAll("/+$", "") + "/" + path.replaceAll("^/+", "");
    }

    private static void printSummary(List<ObjectNode> rows) {
        Map<String, Summary> summaryByStrategy = new LinkedHashMap<>();
        for (ObjectNode row : rows) {
            String strategy = row.path("strategy").asText();
            Summary summary = summaryByStrategy.computeIfAbsent(strategy, k -> new Summary());
            summary.total++;
            if (!row.path("matchedNeedles").isEmpty()) {
                summary.hit++;
            }
            summary.itemTotal += row.path("itemCount").asInt();
            summary.rawTotal += row.path("rawDataCount").asInt();
            summary.evidenceTotal += row.path("evidenceCount").asInt();
        }

        System.out.println();
        System.out.println("=== Summary ===");
        for (Map.Entry<String, Summary> entry : summaryByStrategy.entrySet()) {
            Summary s = entry.getValue();
            double denom = Math.max(1, s.total);
            System.out.printf(Locale.ROOT,
                    "%s | hit=%d/%d | avgItems=%.2f avgRaw=%.2f avgEvidences=%.2f%n",
                    entry.getKey(),
                    s.hit,
                    s.total,
                    s.itemTotal / denom,
                    s.rawTotal / denom,
                    s.evidenceTotal / denom);
        }
    }

    private static final class Summary {
        int total;
        int hit;
        double itemTotal;
        double rawTotal;
        double evidenceTotal;
    }

    private record Options(Path input, Path outputDir, Path outputFile, String serverBaseUrl, List<String> strategies) {
        static Options parse(String[] args) {
            Path input = Path.of("/home/zzx/py/Memind-Local-Dev/benchmark-results/full-pipeline-java/C019-full-results.jsonl");
            Path outputDir = Path.of("/home/zzx/py/Memind-Local-Dev/benchmark-results/regression-c019");
            String server = "http://localhost:8366";
            List<String> strategies = List.of("SIMPLE", "DEEP");
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--input" -> input = Path.of(args[++i]).toAbsolutePath();
                    case "--output-dir" -> outputDir = Path.of(args[++i]).toAbsolutePath();
                    case "--server" -> server = args[++i];
                    case "--strategies" -> strategies = parseStrategies(args[++i]);
                    default -> throw new IllegalArgumentException(
                            "Usage: --input <jsonl> --output-dir <dir> --server <baseUrl> --strategies SIMPLE,DEEP");
                }
            }
            return new Options(input, outputDir, outputDir.resolve("c019-regression-results.jsonl"), server, strategies);
        }

        private static List<String> parseStrategies(String value) {
            Set<String> result = new LinkedHashSet<>();
            for (String part : value.split(",")) {
                String normalized = part.trim().toUpperCase(Locale.ROOT);
                if (!normalized.isEmpty()) {
                    result.add(normalized);
                }
            }
            if (result.isEmpty()) {
                return List.of("SIMPLE", "DEEP");
            }
            return List.copyOf(result);
        }
    }
}
