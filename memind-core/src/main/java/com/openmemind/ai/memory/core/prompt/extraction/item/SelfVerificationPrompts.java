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
package com.openmemind.ai.memory.core.prompt.extraction.item;

import com.openmemind.ai.memory.core.data.DefaultInsightTypes;
import com.openmemind.ai.memory.core.data.MemoryInsightType;
import com.openmemind.ai.memory.core.data.enums.MemoryCategory;
import com.openmemind.ai.memory.core.extraction.item.support.ExtractedMemoryEntry;
import com.openmemind.ai.memory.core.prompt.PromptLanguageRules;
import com.openmemind.ai.memory.core.prompt.PromptRegistry;
import com.openmemind.ai.memory.core.prompt.PromptTemplate;
import com.openmemind.ai.memory.core.prompt.PromptType;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Self-verification prompt builder.
 *
 * <p>Builds a prompt that asks the LLM to find memory items missed during the initial extraction
 * pass. Reuses category classification, identity context, and temporal resolution logic from
 * {@link MemoryItemUnifiedPrompts} for consistency.
 *
 * <p>Returns a {@link PromptTemplate} so the caller can defer language injection to {@code
 * render(language)}.
 */
public final class SelfVerificationPrompts {

    private static final String OBJECTIVE =
            """
            你是一名记忆抽取复核员。上游抽取器已经对对话做过第一遍抽取。你的任务是找出被遗漏的原子事实——也就是文本里清楚存在、但不在已抽取列表中的内容。只返回新的、没有重叠的条目。

            如果没有遗漏，就返回空列表。不要编造或幻觉出新的条目。
            """;

    private static final String PRINCIPLES =
            """
            # 核心原则
            1. 原子性：每个条目必须只表达一个完整连贯的意思。如果一条消息包含多个独立想法，请拆成多个条目。不要把无关事实合并到一个条目里。
            2. 独立性：即使脱离其他条目，也能被单独检索理解。
            3. 内容保真：绝不能丢掉姓名、数字、参数名、版本号、技术术语、配置值、频率、地点或品牌等细节；只能删除填充词。
            4. 显式归因：必须明确说明是谁说了什么或做了什么，并把代词解析为具体名字。
            5. 仅限明确事实：只抽取直接陈述或明确确认的事实，不要猜测。
            6. 不重叠：每个条目都必须覆盖 `AlreadyExtracted` 列表里没有的信息。把已有条目换个说法不算新条目。
            """;

    private static final String EXTRACTION_SCOPE =
            """
            # 抽取范围
            - 用户和助手消息都要抽取。
            - 只有当助手内容包含长期有效的代理指令、可复用任务流程、已解决的问题知识，或具备未来复用价值的具体工具指导时，才保留助手内容。
            - 当用户提问而助手给出稳定修复或可复用工作流时，应抽取可复用的 resolution 或 playbook，而不是只抽取“用户问了 X”，也不要保留“assistant suggested...”这类对话外壳。
            - 不要抽取：问候、闲聊、对助手的夸奖、或缺乏用户具体上下文的空泛套话。
            - 除非用户明确把它采纳为长期习惯、偏好或指令，否则不要抽取助手的情绪支持、鼓励、肯定、反思式提问或治疗式措辞。
            - 一次性的控制消息、临时执行命令和会话管理轮次都不属于持久代理记忆。
            """;

    private static final String MISS_PATTERNS =
            """
            # 常见遗漏模式
            复核时重点关注这些经常被漏掉的模式：
            1. **技术类助手解决方案**：助手消息里的技术方案、配置建议和诊断结论经常在第一遍里被漏掉，它们通常应该变成 resolution 条目。
            2. **问题 + 可用修复**：第一遍可能捕捉到了问题，却漏掉了原因和修复办法。应把它们合并成一个 resolution 条目。
            3. **可复用工作流**：第一遍可能捕捉到了请求，但漏掉了处理这类任务的可复用方法。这些应该变成 playbook 条目。
            4. **长期有效的代理指令**：用户关于代理未来应该如何回应的指令经常被忽略，这些应变成 directive 条目。
            5. **被总结吞掉的具体细节**：版本号、参数值、配置键、品牌名和精确数字等，第一遍可能把它们压缩成了泛化描述。
            6. **多事实消息拆分失败**：一条消息里包含 2 到 3 个不同事实，但第一遍只捕捉到一个。
            7. **团队或项目上下文**：团队成员角色、基础设施细节、项目背景等，第一遍可能当成了噪音。
            不要把支持性或治疗性助手语言当成遗漏的代理记忆。
            """;

    private static final String EXTRACTION_BIAS =
            """
            # 抽取偏向
            - 只添加明确被遗漏的条目。
            - 对 directive、playbook 和 resolution 要保持严格精确。
            - 如果没有清晰证据，就不要添加。
            - 如果在代理记忆和什么都不抽之间犹豫，优先选择什么都不抽。
            """;

    private static final String CATEGORY_CONTEXT_SECTION = "{{CATEGORY_CONTEXT}}";

    private static final String IDENTITY_CONTEXT_SECTION = "{{IDENTITY_CONTEXT}}";

    private static final String SUBJECT_CONTEXT_SECTION = "{{SUBJECT_CONTEXT}}";

    private static final String TEMPORAL_CONTEXT_SECTION = "{{TEMPORAL_CONTEXT}}";

    private static final String SCORING =
            """
            # 评分指南

            ## time
            - 使用 `time` 作为主要时间字段；当源文本里没有语义时间证据时，把 `time` 设为 null。
            - `time.expression`：源文本中的原始时间短语。
            - `time.start`：规范化后的 ISO-8601 UTC 下界。
            - `time.end`：范围或日历桶的 ISO-8601 UTC 上界（不含），单点时间则设为 null。
            - `time.granularity`：`point`、`day`、`week`、`month`、`year`、`range` 或 `unknown` 之一。
            - 有时间含义的记忆：只有当文本本身明确说出或明显暗示那个时间时，才把解析后的绝对日期或区间写进内容并填充 `time`。
            - profile、behavior、directive、playbook、resolution 和 tool 条目通常应把 `time` 设为 null。
            - event 条目只有在文本本身包含明确时间证据时才填写 `time`，例如日期、相对日期短语，或明确的开始/结束标记。
            - 不要把消息时间戳或会话时间戳当成默认时间值；它们只用于解析相对表达，不是持久化默认值。
            - 对于 `day`、`week`、`month` 和 `year`，在转换为 UTC 之前，`time.start` 和 `time.end` 必须在系统时区里构成规范的半开区间。
            - 过渡期间解析器仍然兼容旧字段 `occurredAt`，但你的响应应使用 `time`。
            """;

    private static final String OUTPUT =
            """
            <OutputFormat>
            只返回 JSON 对象，不要有任何额外文本，也不要有 markdown 代码块。
            当没有语义时间证据时，使用 `"time": null`。
            {
              "items": [
                {
                  "content": "一句完整、自包含、保留全部细节的句子",
                  "time": {
                    "expression": "on 2026-03-18 at 10:00 UTC",
                    "start": "2026-03-18T10:00:00Z",
                    "end": null,
                    "granularity": "point"
                  },
                  "insightTypes": ["仅从所分配类别下可用的 insightTypes 中选择"],
                  "category_reason": "关键：简要说明为什么选择这个类别，以及为什么这条内容会被第一遍漏掉。此字段只用于推理，不会被存储。",
                  "category": "<matched_category_from_list>"
                }
              ]
            }
            （如果没有遗漏，返回 `{"items": []}`）
            </OutputFormat>
            """;

    private static final String EXAMPLES =
            """
            # Examples

            ## Good Example 1: First pass merged multiple facts into one item

            AlreadyExtracted:
            - [event] User is migrating payment service from Java 17 to Java 21

            Conversation:
            [2026-03-18 10:01] user: I'm migrating our payment service from Java 17 to 21, \
            using Spring Boot 3.2. Zhang San handles the frontend on our team.

            Output:
            {
              "items": [
                {
                  "content": "User's payment service migration uses Spring Boot 3.2",
                  "time": null,
                  "insightTypes": ["experiences"],
                  "category_reason": "Current project tech stack detail. Missed because the first pass captured the migration but not the specific framework version.",
                  "category": "event"
                },
                {
                  "content": "Zhang San is responsible for frontend in User's team",
                  "time": null,
                  "insightTypes": ["experiences"],
                  "category_reason": "Team member role, current project context. Missed because the first pass focused on the migration fact and overlooked the team structure.",
                  "category": "event"
                }
              ]
            }

            Why good: The first pass only captured the migration. Spring Boot version and team member info were swallowed.

            ## Good Example 2: Assistant solution not captured

            AlreadyExtracted:
            - [event] User encountered HikariCP connection pool exhaustion after enabling virtual threads

            Conversation:
            [2026-03-18 10:04] user: After enabling virtual threads, my HikariCP connection pool keeps getting exhausted.
            [2026-03-18 10:05] assistant: That's because the number of virtual threads far exceeds \
            the pool size limit. Set maximumPoolSize to 10-20 to fix it.

            Output:
            {
              "items": [
                {
                  "content": "Virtual threads caused HikariCP connection pool exhaustion because virtual thread count far exceeds pool size limit; solved by setting maximumPoolSize to 10-20",
                  "time": null,
                  "insightTypes": ["resolutions"],
                  "category_reason": "Resolution item: named problem, cause, and usable fix. The first pass only captured the symptom and missed the fix from the assistant response.",
                  "category": "resolution"
                }
              ]
            }

            Why good: The first pass captured only the symptom. The root cause and solution from the assistant message were missed.

            ## Good Example 3: Durable agent directive missed

            AlreadyExtracted:
            - [profile] User is a backend engineer with 5 years of Java experience
            - [event] User is migrating payment service from Java 17 to Java 21

            Conversation:
            [2026-03-18 10:06] user: From now on, reply in Chinese and keep it concise.

            Output:
            {
              "items": [
                {
                  "content": "User instructed the agent to respond in Chinese and keep answers concise",
                  "time": null,
                  "insightTypes": ["directives"],
                  "category_reason": "Directive item: durable rule for future interaction behavior. Commonly missed because it appears as a brief aside rather than the main task content.",
                  "category": "directive"
                }
              ]
            }

            Why good: User directives to the agent are frequently overlooked during first-pass extraction.

            ## Good Example 4: Reusable workflow missed

            AlreadyExtracted:
            - [event] User wants a comparison between two repositories

            Conversation:
            [2026-03-18 10:08] user: Compare the repositories before proposing changes.
            [2026-03-18 10:09] assistant: First align memory scope, then compare taxonomy, extraction flow, and storage path.

            Output:
            {
              "items": [
                {
                  "content": "For repository comparisons, first align memory scope, then compare taxonomy, extraction flow, and storage path",
                  "time": null,
                  "insightTypes": ["playbooks"],
                  "category_reason": "Playbook item: reusable handling workflow for a recurring class of tasks. The first pass captured the request but missed the reusable method.",
                  "category": "playbook"
                }
              ]
            }

            Why good: The reusable method is the memory, not the one-off request title.

            ## Good Example 5: Nothing missed — return empty

            AlreadyExtracted:
            - [profile] User is a backend engineer with 5 years of Java experience
            - [event] User started migrating payment service from Java 17 to Java 21

            Conversation:
            [2026-03-18 10:00] user: I'm a backend engineer, been writing Java for 5 years. \
            Recently started migrating our payment service from 17 to 21.

            Output:
            {"items": []}

            Why good: All facts in the conversation are already covered. No duplicates generated.

            ## Bad Example 1: Supportive assistant language (WRONG)

            Conversation:
            [2026-03-18 10:07] assistant: When you feel like you lost, try asking yourself \
            what else you are feeling without judgment.

            Output (WRONG):
            {
              "items": [
                {
                  "content": "Assistant suggested that User ask what else they are feeling without judgment",
                  "category": "directive"
                }
              ]
            }

            -> Wrong: Supportive or therapeutic assistant language is not a missed agent memory \
            unless the user later adopts it as a lasting routine or instruction.

            ## Bad Example 2: One-off control message (WRONG)

            Conversation:
            [2026-03-18 10:11] user: continue

            Output (WRONG):
            {
              "items": [
                {
                  "content": "User told the agent to continue",
                  "category": "directive"
                }
              ]
            }

            -> Wrong: one-off control messages are not durable agent memory.

            ## Bad Example 3: Rephrasing an existing item (WRONG)

            AlreadyExtracted:
            - [profile] User is a backend engineer with 5 years of Java experience

            Output (WRONG):
            {
              "items": [
                {
                  "content": "User has been writing Java for 5 years",
                  "category": "profile"
                }
              ]
            }

            -> Wrong: This is just a rephrase of an already-extracted item, not a new fact.\
            """;

    private static final String USER_PROMPT_TEMPLATE =
            """
            Please identify any missed memory items from the following conversation.

            # AlreadyExtracted
            {{existing_entries}}

            # Conversation
            {{original_text}}\
            """;

    private SelfVerificationPrompts() {}

    /**
     * Builds a self-verification prompt template.
     *
     * @param originalText original conversation text
     * @param existingEntries already-extracted memory entries
     * @param referenceTime reference time for resolving relative dates (null allowed)
     * @param insightTypes available insight types for categorization
     * @param userName user name (replaces "User" in prompt when not empty)
     * @param categories categories to include in the prompt (null = all)
     * @return prompt template; call {@code render(language)} to produce the final result
     */
    public static PromptTemplate build(
            String originalText,
            List<ExtractedMemoryEntry> existingEntries,
            Instant referenceTime,
            List<MemoryInsightType> insightTypes,
            String userName,
            Set<MemoryCategory> categories) {
        return build(
                PromptRegistry.EMPTY,
                originalText,
                existingEntries,
                referenceTime,
                insightTypes,
                userName,
                categories);
    }

    public static PromptTemplate buildDefault() {
        return defaultBuilder().build();
    }

    public static PromptTemplate buildPreview() {
        return defaultBuilder()
                .variable(
                        "CATEGORY_CONTEXT",
                        MemoryItemUnifiedPrompts.buildCategoryContext(
                                null, DefaultInsightTypes.all()))
                .variable("IDENTITY_CONTEXT", MemoryItemUnifiedPrompts.buildIdentityContext("Ada"))
                .variable(
                        "SUBJECT_CONTEXT",
                        MemoryItemUnifiedPrompts.buildSubjectClarityContext("Ada")
                                + "\nReject any item if a reader cannot identify who each pronoun"
                                + " refers to without reading the original conversation.")
                .variable(
                        "TEMPORAL_CONTEXT",
                        MemoryItemUnifiedPrompts.buildTimeContext(
                                null, Instant.parse("2026-03-29T00:00:00Z")))
                .build();
    }

    public static PromptTemplate build(
            PromptRegistry registry,
            String originalText,
            List<ExtractedMemoryEntry> existingEntries,
            Instant referenceTime,
            List<MemoryInsightType> insightTypes,
            String userName,
            Set<MemoryCategory> categories) {

        PromptTemplate.Builder builder =
                registry.hasOverride(PromptType.SELF_VERIFICATION)
                        ? PromptTemplate.builder("self-verification")
                                .section(
                                        "system",
                                        registry.getOverride(PromptType.SELF_VERIFICATION))
                        : defaultBuilder();

        return builder.userPrompt(USER_PROMPT_TEMPLATE)
                .variable(
                        "CATEGORY_CONTEXT",
                        MemoryItemUnifiedPrompts.buildCategoryContext(categories, insightTypes))
                .variable(
                        "IDENTITY_CONTEXT", MemoryItemUnifiedPrompts.buildIdentityContext(userName))
                .variable(
                        "SUBJECT_CONTEXT",
                        MemoryItemUnifiedPrompts.buildSubjectClarityContext(userName)
                                + "\nReject any item if a reader cannot identify who each pronoun"
                                + " refers to without reading the original conversation.")
                .variable(
                        "TEMPORAL_CONTEXT",
                        MemoryItemUnifiedPrompts.buildTimeContext(originalText, referenceTime))
                .variable("existing_entries", formatExistingEntries(existingEntries))
                .variable("original_text", originalText != null ? originalText : "")
                .build();
    }

    private static PromptTemplate.Builder defaultBuilder() {
        return PromptTemplate.builder("self-verification")
                .section("objective", OBJECTIVE)
                .section("principles", PRINCIPLES)
                .section("extractionScope", EXTRACTION_SCOPE)
                .section("missPatterns", MISS_PATTERNS)
                .section("extractionBias", EXTRACTION_BIAS)
                .section("outputLanguage", PromptLanguageRules.MATCH_SOURCE_LANGUAGE)
                .section("categoryContext", CATEGORY_CONTEXT_SECTION)
                .section("identityContext", IDENTITY_CONTEXT_SECTION)
                .section("subjectContext", SUBJECT_CONTEXT_SECTION)
                .section("temporalContext", TEMPORAL_CONTEXT_SECTION)
                .section("scoring", SCORING)
                .section("output", OUTPUT)
                .section("examples", EXAMPLES);
    }

    private static String formatExistingEntries(List<ExtractedMemoryEntry> entries) {
        if (entries == null || entries.isEmpty()) {
            return "(none -- this is the first extraction pass)";
        }
        return entries.stream()
                .map(
                        e ->
                                "- ["
                                        + (e.category() != null ? e.category() : "unknown")
                                        + "] "
                                        + e.content())
                .collect(Collectors.joining("\n"));
    }
}
