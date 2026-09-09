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
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class FullQaEvaluationRunner {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();
    private static final Path PROJECT = locateProjectRoot();
    private static final Path TEST_RESULT_DIR = PROJECT.resolve("benchmark-results/full-pipeline-java");
    private static final Path EVAL_RESULT_DIR = PROJECT.resolve("benchmark-results/full-evaluation-java");
    private static final Path DATA_DIR = Path.of("/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick");
    private static final Map<String, String> DOT_ENV = loadDotEnv(PROJECT.resolve(".env"));
    private static final Pattern DIGITS = Pattern.compile("-?\\d+(?:\\.\\d+)?");
    private static final String DEFAULT_JUDGE_PROMPT =
            "你是一个金融数据评估专家。请判断\"检索到的记忆\"是否包含了\"标准答案\"中的关键事实信息。\n\n"
                    + "标准答案: %s\n\n检索到的记忆: %s\n\n只回答一个数字:\n"
                    + "- 2: 检索结果完全包含了标准答案的关键事实（数值、日期、名称等核心信息一致）\n"
                    + "- 1: 检索结果部分包含了标准答案的关键事实\n"
                    + "- 0: 检索结果不包含标准答案的关键事实\n\n只回答数字(0/1/2):";

    private FullQaEvaluationRunner() {}

    public static void main(String[] args) throws Exception {
        Options options = Options.parse(args);
        Files.createDirectories(EVAL_RESULT_DIR);
        List<Path> inputFiles = resolveInputFiles(options.input);
        List<ObjectNode> allRecords = new ArrayList<>();
        List<ObjectNode> retrievalScores = new ArrayList<>();
        List<ObjectNode> answerScores = new ArrayList<>();

        for (Path file : inputFiles) {
            List<JsonNode> records = readJsonl(file);
            for (JsonNode record : records) {
                ObjectNode retrieval = evaluateRetrieval(record, options);
                ObjectNode answer = evaluateAnswer(record, options);
                retrievalScores.add(retrieval);
                answerScores.add(answer);
                ObjectNode merged = JSON.createObjectNode();
                merged.setAll((ObjectNode) record);
                merged.set("retrievalEvaluation", retrieval);
                merged.set("answerEvaluation", answer);
                allRecords.add(merged);
            }
        }

        writeJsonl(EVAL_RESULT_DIR.resolve("retrieval-scores.jsonl"), retrievalScores);
        writeJsonl(EVAL_RESULT_DIR.resolve("answer-scores.jsonl"), answerScores);
        ObjectNode summary = buildSummary(allRecords, retrievalScores, answerScores);
        Files.writeString(
                EVAL_RESULT_DIR.resolve("summary.json"),
                JSON.writerWithDefaultPrettyPrinter().writeValueAsString(summary) + System.lineSeparator());

        System.out.println("\n=== 评测完成 ===");
        System.out.println(JSON.writerWithDefaultPrettyPrinter().writeValueAsString(summary));
    }

    private static ObjectNode evaluateRetrieval(JsonNode record, Options options) throws Exception {
        String groundTruth = record.path("expectedAnswer").asText();
        List<JsonNode> items = nodes(record.path("retrievedItems"));
        List<Integer> scores = new ArrayList<>();
        for (JsonNode item : items) {
            scores.add(judgeScore(groundTruth, item.path("text").asText(""), options, "retrieval"));
        }

        int top1Score = scores.isEmpty() ? 0 : scores.get(0);
        int bestScore = scores.stream().mapToInt(Integer::intValue).max().orElse(0);
        int firstRelevantRank = firstRelevantRank(scores);
        double mrr = firstRelevantRank == -1 ? 0.0 : 1.0 / firstRelevantRank;
        double ndcg = ndcg(scores);
        double precision = scores.isEmpty()
                ? 0.0
                : scores.stream().filter(score -> score > 0).count() / (double) scores.size();
        double recall = scores.stream().anyMatch(score -> score > 0) ? 1.0 : 0.0;
        double fullRecall = scores.stream().anyMatch(score -> score == 2) ? 1.0 : 0.0;

        ObjectNode node = JSON.createObjectNode();
        node.put("companyId", record.path("companyId").asText());
        node.put("companyName", record.path("companyName").asText());
        node.put("qaId", record.path("qaId").asText());
        node.put("groundTruth", groundTruth);
        node.put("retrievalTop1Score", top1Score);
        node.put("retrievalBestScore", bestScore);
        node.put("retrievalAvgScore", averageIntList(scores));
        node.put("retrievalRecall", recall);
        node.put("retrievalFullRecall", fullRecall);
        node.put("retrievalPrecision", precision);
        node.put("retrievalMRR", mrr);
        node.put("retrievalNDCG", ndcg);
        node.put("retrievalFirstRelevantRank", firstRelevantRank);
        node.put("retrievalScores", toArray(scores));
        node.put("retrievedText", joinTexts(items));
        return node;
    }

    private static ObjectNode evaluateAnswer(JsonNode record, Options options) throws Exception {
        String groundTruth = record.path("expectedAnswer").asText();
        String answer = record.path("generated").path("answer").asText("");
        int judge = judgeScore(groundTruth, answer, options, "answer");
        ObjectNode node = JSON.createObjectNode();
        node.put("companyId", record.path("companyId").asText());
        node.put("companyName", record.path("companyName").asText());
        node.put("qaId", record.path("qaId").asText());
        node.put("answerScore", judge);
        node.put("generatedAnswer", answer);
        node.put("groundTruth", groundTruth);
        node.put("answerType", record.path("answerType").asText());
        node.put("abstained", record.path("generated").path("abstained").asBoolean(false));
        return node;
    }

    private static ObjectNode buildSummary(
            List<ObjectNode> allRecords,
            List<ObjectNode> retrievalScores,
            List<ObjectNode> answerScores) {
        ObjectNode summary = JSON.createObjectNode();
        summary.put("total", allRecords.size());
        summary.put("retrievalAvgScore", averageInt(retrievalScores, "retrievalTop1Score"));
        summary.put("retrievalBestAvgScore", averageInt(retrievalScores, "retrievalBestScore"));
        summary.put("retrievalRecallRate", ratioDouble(retrievalScores, "retrievalRecall", 1.0));
        summary.put("retrievalFullRecallRate", ratioDouble(retrievalScores, "retrievalFullRecall", 1.0));
        summary.put("retrievalMRR", averageDouble(retrievalScores, "retrievalMRR"));
        summary.put("retrievalNDCG", averageDouble(retrievalScores, "retrievalNDCG"));
        summary.put("retrievalPrecision", averageDouble(retrievalScores, "retrievalPrecision"));
        summary.put("answerAvgScore", averageInt(answerScores, "answerScore"));
        summary.put("answerScore2Rate", ratio(answerScores, "answerScore", 2));
        summary.put("answerScore1Rate", ratio(answerScores, "answerScore", 1));
        summary.put("answerScore0Rate", ratio(answerScores, "answerScore", 0));

        Map<String, CompanySummary> companySummaries = new HashMap<>();
        for (ObjectNode record : allRecords) {
            String companyId = record.path("companyId").asText();
            CompanySummary company = companySummaries.computeIfAbsent(companyId, id -> new CompanySummary());
            company.name = record.path("companyName").asText();
            company.total++;
            company.retrievalTop1 += record.path("retrievalEvaluation").path("retrievalTop1Score").asInt();
            company.retrievalBest += record.path("retrievalEvaluation").path("retrievalBestScore").asInt();
            company.retrievalRecall += record.path("retrievalEvaluation").path("retrievalRecall").asDouble();
            company.retrievalFullRecall += record.path("retrievalEvaluation").path("retrievalFullRecall").asDouble();
            company.retrievalMRR += record.path("retrievalEvaluation").path("retrievalMRR").asDouble();
            company.retrievalNDCG += record.path("retrievalEvaluation").path("retrievalNDCG").asDouble();
            company.answer += record.path("answerEvaluation").path("answerScore").asInt();
        }
        ArrayNode companies = summary.putArray("companies");
        companySummaries.entrySet().stream()
                .sorted(Comparator.comparing(Map.Entry::getKey))
                .forEach(entry -> {
                    CompanySummary company = entry.getValue();
                    ObjectNode node = companies.addObject();
                    node.put("companyId", entry.getKey());
                    node.put("companyName", company.name);
                    node.put("total", company.total);
                    node.put("retrievalTop1AvgScore", company.total == 0 ? 0.0 : company.retrievalTop1 / (double) company.total);
                    node.put("retrievalBestAvgScore", company.total == 0 ? 0.0 : company.retrievalBest / (double) company.total);
                    node.put("retrievalRecallRate", company.total == 0 ? 0.0 : company.retrievalRecall / (double) company.total);
                    node.put("retrievalFullRecallRate", company.total == 0 ? 0.0 : company.retrievalFullRecall / (double) company.total);
                    node.put("retrievalMRR", company.total == 0 ? 0.0 : company.retrievalMRR / (double) company.total);
                    node.put("retrievalNDCG", company.total == 0 ? 0.0 : company.retrievalNDCG / (double) company.total);
                    node.put("answerAvgScore", company.total == 0 ? 0.0 : company.answer / (double) company.total);
                });
        summary.put("testedAt", Instant.now().toString());
        return summary;
    }

    private static double averageInt(List<ObjectNode> records, String field) {
        if (records.isEmpty()) {
            return 0.0;
        }
        return records.stream().mapToInt(record -> record.path(field).asInt()).average().orElse(0.0);
    }

    private static double averageDouble(List<ObjectNode> records, String field) {
        if (records.isEmpty()) {
            return 0.0;
        }
        return records.stream().mapToDouble(record -> record.path(field).asDouble()).average().orElse(0.0);
    }

    private static double averageIntList(List<Integer> scores) {
        if (scores.isEmpty()) {
            return 0.0;
        }
        return scores.stream().mapToInt(Integer::intValue).average().orElse(0.0);
    }

    private static List<JsonNode> nodes(JsonNode node) {
        List<JsonNode> result = new ArrayList<>();
        if (node != null && node.isArray()) {
            for (JsonNode item : node) {
                result.add(item);
            }
        }
        return result;
    }

    private static double ratio(List<ObjectNode> records, String field, int target) {
        if (records.isEmpty()) {
            return 0.0;
        }
        long matches = records.stream().filter(record -> record.path(field).asInt() == target).count();
        return matches / (double) records.size();
    }

    private static double ratioDouble(List<ObjectNode> records, String field, double target) {
        if (records.isEmpty()) {
            return 0.0;
        }
        long matches = records.stream().filter(record -> Double.compare(record.path(field).asDouble(), target) == 0).count();
        return matches / (double) records.size();
    }

    private static int firstRelevantRank(List<Integer> scores) {
        for (int i = 0; i < scores.size(); i++) {
            if (scores.get(i) > 0) {
                return i + 1;
            }
        }
        return -1;
    }

    private static double ndcg(List<Integer> scores) {
        if (scores.isEmpty()) {
            return 0.0;
        }
        double dcg = 0.0;
        for (int i = 0; i < scores.size(); i++) {
            double gain = Math.pow(2.0, scores.get(i)) - 1.0;
            dcg += gain / log2(i + 2);
        }
        List<Integer> ideal = new ArrayList<>(scores);
        ideal.sort(Comparator.reverseOrder());
        double idcg = 0.0;
        for (int i = 0; i < ideal.size(); i++) {
            double gain = Math.pow(2.0, ideal.get(i)) - 1.0;
            idcg += gain / log2(i + 2);
        }
        return idcg == 0.0 ? 0.0 : dcg / idcg;
    }

    private static double log2(int value) {
        return Math.log(value) / Math.log(2.0);
    }

    private static ArrayNode toArray(List<Integer> scores) {
        ArrayNode array = JSON.createArrayNode();
        for (Integer score : scores) {
            array.add(score);
        }
        return array;
    }

    private static void writeJsonl(Path path, List<ObjectNode> records) throws IOException {
        Files.createDirectories(path.getParent());
        StringBuilder output = new StringBuilder();
        for (ObjectNode record : records) {
            output.append(JSON.writeValueAsString(record)).append(System.lineSeparator());
        }
        Files.writeString(path, output.toString());
    }

    private static List<Path> resolveInputFiles(String input) {
        List<Path> files = new ArrayList<>();
        if (input == null || input.isBlank()) {
            for (String companyId : List.of("C016", "C017", "C018", "C019", "C020")) {
                Path file = TEST_RESULT_DIR.resolve(companyId + "-full-results.jsonl");
                if (Files.isRegularFile(file)) {
                    files.add(file);
                }
            }
            if (files.isEmpty()) {
                throw new IllegalStateException("No test result files found in " + TEST_RESULT_DIR);
            }
            return files;
        }
        for (String part : input.split(",")) {
            Path file = Path.of(part.trim()).toAbsolutePath();
            if (!Files.isRegularFile(file)) {
                throw new IllegalArgumentException("Input file not found: " + file);
            }
            files.add(file);
        }
        return files;
    }

    private static List<JsonNode> readJsonl(Path path) throws IOException {
        List<JsonNode> records = new ArrayList<>();
        for (String line : Files.readAllLines(path)) {
            if (!line.isBlank()) {
                records.add(JSON.readTree(line));
            }
        }
        return records;
    }

    private static int judgeScore(String groundTruth, String text, Options options, String kind) throws Exception {
        String prompt = String.format(Locale.ROOT, options.judgePrompt, groundTruth, text);
        ObjectNode body = JSON.createObjectNode();
        body.put("model", options.model);
        body.putArray("messages").addObject()
                .put("role", "user")
                .put("content", prompt);
        body.put("temperature", 0.0).put("max_tokens", 16);
        Response response = post(
                joinUrl(options.modelUrl, "chat/completions"),
                body,
                "Bearer " + options.apiKey,
                options.timeoutSeconds);
        if (response.status < 200 || response.status >= 300) {
            throw new IllegalStateException(kind + " judge failed: HTTP " + response.status + " " + response.body);
        }
        JsonNode root = JSON.readTree(response.body);
        JsonNode choices = root.path("choices");
        if (!choices.isArray() || choices.isEmpty()) {
            throw new IllegalStateException(kind + " judge returned no choices: " + response.body);
        }
        String content = choices.get(0).path("message").path("content").asText("").trim();
        Matcher matcher = Pattern.compile("(?<!\\d)[012](?!\\d)").matcher(content);
        if (matcher.find()) {
            return Integer.parseInt(matcher.group());
        }
        if (content.contains("2")) {
            return 2;
        }
        if (content.contains("1")) {
            return 1;
        }
        return 0;
    }

    private static String joinTexts(List<JsonNode> items) {
        StringBuilder builder = new StringBuilder();
        for (JsonNode item : items) {
            if (builder.length() > 0) {
                builder.append(' ');
            }
            builder.append(item.path("text").asText(""));
        }
        return builder.toString();
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

    private static Path locateProjectRoot() {
        Path current = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        Path candidate = current;
        while (candidate != null) {
            if (Files.isRegularFile(candidate.resolve(".env")) && Files.isDirectory(candidate.resolve("dev-tools"))) {
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
                if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
                    value = value.substring(1, value.length() - 1);
                }
                values.put(key, value);
            }
        } catch (IOException error) {
            throw new IllegalStateException("Unable to read .env: " + path, error);
        }
        return values;
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        if (value != null && !value.isBlank()) {
            return value;
        }
        String dotenv = DOT_ENV.get(name);
        return dotenv == null || dotenv.isBlank() ? fallback : dotenv;
    }

    private static class CompanySummary {
        String name = "";
        int total;
        double retrievalTop1;
        double retrievalBest;
        double retrievalRecall;
        double retrievalFullRecall;
        double retrievalMRR;
        double retrievalNDCG;
        double answer;
    }

    private record Response(int status, String body) {}

    private record Options(
            String input,
            int timeoutSeconds,
            String modelUrl,
            String apiKey,
            String model,
            String judgePrompt) {
        static Options parse(String[] args) {
            String input = null;
            int timeoutSeconds = 300;
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--input" -> input = args[++i];
                    case "--timeout" -> timeoutSeconds = Integer.parseInt(args[++i]);
                    default -> throw new IllegalArgumentException(
                            "Usage: [--input file1.jsonl,file2.jsonl] [--timeout seconds]");
                }
            }
            String modelUrl = env("OPENAI_BASE_URL", "");
            String apiKey = env("OPENAI_API_KEY", "");
            String model = env("OPENAI_CHAT_MODEL", "");
            if (modelUrl.isBlank() || apiKey.isBlank() || model.isBlank()) {
                throw new IllegalStateException(
                        "Missing model configuration: OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_CHAT_MODEL");
            }
            return new Options(input, timeoutSeconds, modelUrl, apiKey, model, DEFAULT_JUDGE_PROMPT);
        }
    }
}
