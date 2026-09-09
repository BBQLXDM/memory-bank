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

import com.openmemind.ai.memory.core.prompt.PromptLanguageRules;
import com.openmemind.ai.memory.core.prompt.PromptRegistry;
import com.openmemind.ai.memory.core.prompt.PromptTemplate;
import com.openmemind.ai.memory.core.prompt.PromptType;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.regex.Pattern;

/**
 * Foresight extraction prompt builder.
 *
 * <p>Generates associative predictions about the user's future behaviors, needs, and likely actions
 * based on conversation content. Returns a {@link PromptTemplate} so the caller can defer language
 * injection to {@code render(language)}.
 */
public final class ForesightPrompts {

    private static final DateTimeFormatter DATE_FMT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC);

    private static final Pattern MESSAGE_TIMESTAMP_PATTERN =
            Pattern.compile("\\[\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}]");

    private static final String OBJECTIVE =
            """
            你是一名预测分析师。你的任务是分析一段对话，并生成关于用户未来行为、需求和可能动作的前瞻性预测。

            上游抽取器已经捕捉到了这段对话中的明确事实（已说出的计划、偏好、事件）。你的工作不同——你必须预测这些事实之后会发生什么。请生成关于行为变化、潜在需求以及用户未明确说出的后续动作的关联性预测。
            """;

    private static final String GUIDELINES =
            """
            # 核心原则

            1. **预测，不要复述**：每条预测都必须超出用户已经明确说出的内容。比如用户说“我周五有面试”，那是事实抽取的内容；你应该预测这件事会引发什么行为变化（例如更集中练习面试、需要模拟面试帮助）。
            2. **基于证据**：每条预测都必须建立在对话中的明确线索上（已说出的计划、描述的处境、重复模式、未解决问题）。没有文本支持就不要猜。
            3. **具体且可执行**：每条预测都要足够具体，系统才能据此行动（例如主动提供帮助、展示相关信息），不要写成“用户未来可能需要帮助”这种空话。
            4. **有时间边界**：每条预测都必须包含有效期，过期预测是噪音。
            5. **场景匹配语言**：生活场景（健康、家庭、爱好）用日常语言；工作场景（项目、职业、技能）用专业语言。

            # 要预测什么

            - **行为变化**：用户基于当前讨论很可能会采取的动作（例如术后饮食变化、考试前练习重点转移）。
            - **潜在需求**：用户根据当前处境很快可能需要的帮助或资源（例如模拟面试练习、新服务上线后的排障帮助）。
            - **后续动作**：用户虽然没明说，但从当前处境逻辑上会跟进的下一步（例如学了新框架后，会尝试把它用到项目里）。
            - **重复模式**：如果对话揭示出某种模式，就预测其延续（例如用户每周二跑步 → 下周二大概率还会跑）。

            # 不要预测什么

            - **复述事实**："用户下周五有面试"——这只是事实，不是预测，上游已经会抽取。
            - **换个说法复述计划**："用户会参加面试"——只是换句话说，不是预测。
            - **用户明确承诺的未来事项**："用户说他会在某天前完成 X"——这是已抽取的 FACT，应该预测由此带来的后果，而不是承诺本身。
            - **空泛猜测**："用户以后可能会需要帮助"——不可执行。
            - **情绪或人格推断**："用户看起来对面试焦虑"——这不是行为预测。
            - **超过 90 天的预测**：除非对话中有明确支持。

            # 时间估计

            - 对话里明确给出截止日期 → `validUntil` = 该精确日期。
            - “下周”“很快”“短期任务” → `durationDays` = 7 到 14。
            - 重复习惯 / 技能应用 → `durationDays` = 30。
            - 长期目标 / 职业规划 → `durationDays` = 60 到 90。
            - 不确定时 → 默认 `durationDays` = 30。

            优先提取对话中的明确时间引用；只有没有时间线索时才估算。
            """;

    private static final String OUTPUT =
            """
            # 输出格式

            仅返回 JSON 对象，不要有任何额外文本，也不要有 markdown 代码块。
            只返回合法 JSON，不要前后包裹其他内容。
            生成 2 到 6 条预测；如果没有可预测信号，就返回空列表。

            {
              "items": [
                {
                  "content": "关于未来行为或需求的具体预测（一句话）",
                  "evidence": "支持这条预测的对话摘录或简述",
                  "validUntil": "2026-03-28",
                  "durationDays": 7
                }
              ]
            }

            （如果没有符合条件的内容，返回 `{"items": []}`）
            """;

    private static final String EXAMPLES =
            """
            # Examples

            ## Good Example 1: Life scenario (medical)

            Conversation:
            [2026-03-15 14:05] user: Just got my wisdom tooth extracted. Still a bit sore.
            [2026-03-15 14:06] assistant: Make sure to keep the area clean and avoid hard foods.
            [2026-03-15 14:07] user: The dentist said to come back if the swelling gets worse.

            Output:
            {
              "items": [
                {
                  "content": "User will avoid spicy and hot foods for the next week",
                  "evidence": "Wisdom tooth extraction; dentist advised keeping area clean",
                  "validUntil": "2026-03-22",
                  "durationDays": 7
                },
                {
                  "content": "User will prefer soft foods and reduce chewing force for several days",
                  "evidence": "User reported soreness after extraction",
                  "validUntil": "2026-03-19",
                  "durationDays": 4
                },
                {
                  "content": "User may need a follow-up dental visit if swelling worsens",
                  "evidence": "Dentist instructed to return if swelling gets worse",
                  "validUntil": "2026-03-29",
                  "durationDays": 14
                }
              ]
            }

            Why this is good:
            - None of these predictions are explicitly stated by the user
            - Each is a logical behavioral consequence of the tooth extraction
            - "Avoid spicy foods" and "prefer soft foods" are inferred from the medical \
            situation, not restated from the conversation
            - Time estimates are reasonable for post-extraction recovery

            ## Good Example 2: Work scenario (learning new technology)

            Conversation:
            [2026-03-15 10:00] user: I just finished the Spring Boot 3.2 virtual threads workshop
            [2026-03-15 10:02] user: The structured concurrency part was really eye-opening
            [2026-03-15 10:03] assistant: Virtual threads can dramatically simplify concurrent code...

            Output:
            {
              "items": [
                {
                  "content": "User will likely try enabling virtual threads in their current Spring Boot project",
                  "evidence": "Completed virtual threads workshop; found structured concurrency eye-opening",
                  "validUntil": "2026-04-14",
                  "durationDays": 30
                },
                {
                  "content": "User may need help migrating thread pool configurations when adopting virtual threads",
                  "evidence": "Learning virtual threads implies upcoming migration from platform threads",
                  "validUntil": "2026-04-14",
                  "durationDays": 30
                },
                {
                  "content": "User will likely explore structured concurrency APIs (StructuredTaskScope) in more depth",
                  "evidence": "User specifically highlighted structured concurrency as eye-opening",
                  "validUntil": "2026-04-14",
                  "durationDays": 30
                }
              ]
            }

            Why this is good:
            - Predicts what the user will DO with the knowledge, not just restating \
            "user learned about virtual threads"
            - "Need help migrating thread pool configs" is an inferred emerging need
            - "Explore StructuredTaskScope" follows from the expressed interest

            ## Bad Example 1: Restating facts (WRONG)

            Conversation: User mentions they have a Google interview next Friday.

            Output (WRONG):
            {
              "items": [
                {
                  "content": "User has a Google interview next Friday",
                  "evidence": "User stated they have a Google interview",
                  "validUntil": "2026-03-22",
                  "durationDays": 7
                }
              ]
            }

            -> Wrong: This is a fact, not a prediction. The upstream fact extractor already \
            captured "user has a Google interview next Friday". Foresight should predict \
            behavioral changes: "User will focus coding practice on algorithm problems this \
            week", "User may need mock behavioral interview practice before Friday".

            ## Bad Example 3: Restating user-committed plans (WRONG)

            Conversation:
            user: I need to finish the load test script by Wednesday.
            user: The test report must be submitted to the CTO by Friday.

            Output (WRONG):
            {
              "items": [
                {
                  "content": "User will complete the load test script by Wednesday",
                  "evidence": "User stated the script deadline is Wednesday",
                  "validUntil": "2026-03-19",
                  "durationDays": 4
                },
                {
                  "content": "User will submit the test report to the CTO by Friday",
                  "evidence": "User mentioned the Friday submission deadline",
                  "validUntil": "2026-03-21",
                  "durationDays": 6
                }
              ]
            }

            -> Wrong: These are explicit commitments the user stated — they are FACTs already \
            captured upstream. Foresight should predict what happens BECAUSE of these \
            deadlines, not the deadlines themselves. Good predictions here would be: \
            "User will be under time pressure and may need help optimizing Gatling scripts \
            quickly", "If load test results don't meet targets, user will need specific \
            bottleneck analysis and optimization strategies".

            ## Bad Example 2: Too vague (WRONG)
            {
              "items": [
                {
                  "content": "User might need help with their project",
                  "evidence": "User is working on a project",
                  "validUntil": "2026-04-15",
                  "durationDays": 30
                }
              ]
            }

            -> Wrong: "Might need help with their project" is not actionable. What kind of \
            help? What aspect of the project? A prediction must be specific enough that a \
            system could prepare relevant assistance.

            ## Example 3: No predictable signals

            Conversation:
            user: What's the weather like today?
            assistant: I can't check the weather, but you can try a weather app.

            Output:
            {"items": []}\
            """;

    private static final String USER_PROMPT_TEMPLATE =
            """
            Please extract foresight predictions from the following conversation:

            # Conversation

            {{segment_text}}\
            """;

    private ForesightPrompts() {}

    /**
     * Builds a foresight extraction prompt template.
     *
     * @param segmentText conversation text to predict from
     * @param referenceTime reference time for calculating validUntil dates (null allowed)
     * @return prompt template; call {@code render(language)} to produce the final result
     */
    public static PromptTemplate build(String segmentText, Instant referenceTime) {
        return build(PromptRegistry.EMPTY, segmentText, referenceTime);
    }

    public static PromptTemplate buildDefault() {
        return defaultBuilder(buildTimeContext(null, null)).build();
    }

    public static PromptTemplate build(
            PromptRegistry registry, String segmentText, Instant referenceTime) {
        String timeCtx = buildTimeContext(segmentText, referenceTime);

        PromptTemplate.Builder builder =
                registry.hasOverride(PromptType.FORESIGHT)
                        ? PromptTemplate.builder("foresight-extraction")
                                .section("system", registry.getOverride(PromptType.FORESIGHT))
                        : defaultBuilder(timeCtx);

        return builder.userPrompt(USER_PROMPT_TEMPLATE)
                .variable("segment_text", segmentText != null ? segmentText : "")
                .build();
    }

    private static PromptTemplate.Builder defaultBuilder(String timeContext) {
        return PromptTemplate.builder("foresight-extraction")
                .section("objective", OBJECTIVE)
                .section("guidelines", GUIDELINES)
                .section("outputLanguage", PromptLanguageRules.MATCH_SOURCE_LANGUAGE)
                .section("output", OUTPUT)
                .section("examples", EXAMPLES)
                .section("timeContext", timeContext);
    }

    private static String buildTimeContext(String segmentText, Instant referenceTime) {
        boolean hasTimestamps =
                segmentText != null && MESSAGE_TIMESTAMP_PATTERN.matcher(segmentText).find();

        if (hasTimestamps) {
            String fallback =
                    referenceTime != null
                            ? "\nFallback Reference Date (use only if a message lacks a"
                                    + " timestamp): "
                                    + DATE_FMT.format(referenceTime)
                            : "";
            return "# Time Context\n"
                    + "Messages contain timestamps (e.g., [2026-03-01 14:30]). Use the latest"
                    + " message's timestamp as the baseline to calculate `validUntil` dates."
                    + fallback;
        } else if (referenceTime != null) {
            return "# Time Context\n"
                    + "Today's Reference Date: "
                    + DATE_FMT.format(referenceTime)
                    + ". Use this as the baseline to calculate `validUntil` dates.";
        }

        return "# Time Context\n"
                + "No temporal anchors available. Estimate `validUntil` relatively if needed.";
    }
}
