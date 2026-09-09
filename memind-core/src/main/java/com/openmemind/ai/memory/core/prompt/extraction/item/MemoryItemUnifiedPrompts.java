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

import com.openmemind.ai.memory.core.builder.ItemGraphOptions;
import com.openmemind.ai.memory.core.data.DefaultInsightTypes;
import com.openmemind.ai.memory.core.data.MemoryInsightType;
import com.openmemind.ai.memory.core.data.enums.MemoryCategory;
import com.openmemind.ai.memory.core.data.enums.MemoryScope;
import com.openmemind.ai.memory.core.prompt.PromptLanguageRules;
import com.openmemind.ai.memory.core.prompt.PromptRegistry;
import com.openmemind.ai.memory.core.prompt.PromptTemplate;
import com.openmemind.ai.memory.core.prompt.PromptType;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.EnumSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * Unified mode memory item extraction prompt builder.
 *
 * <p>Extracts atomic facts from conversation segments across all MemoryCategory types. Optimized
 * with Chain-of-Thought (category_reason) and Contrastive Examples for small models.
 */
public final class MemoryItemUnifiedPrompts {

    static final DateTimeFormatter DATE_FMT = DateTimeFormatter.ISO_LOCAL_DATE;

    static final Pattern MESSAGE_TIMESTAMP_PATTERN =
            Pattern.compile("\\[\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}]");

    static final String RESOLVE_DATES_INSTRUCTION =
            """
            Resolve relative expressions ("yesterday", "next month") to absolute dates. \
            Use `time` as the primary temporal field. Set `time` to null when the text itself \
            does not provide semantic temporal evidence for the memory. Do NOT infer `time` \
            from the reference date or message timestamp alone.\
            """;

    // ── System & User Prompt Templates ───────────────────────────────────────

    private static final String OBJECTIVE =
            """
            你是一名专家级信息抽取分析师。请分析源文本并抽取适合检索的原子记忆项。

            只抽取具有长期检索价值、且自包含的事实。为每条记忆项分配正确的类别。
            如果没有有效信息，则返回空列表。
            """;

    private static final String PRINCIPLES =
            """
            # 核心原则
            1. 原子性：每条记忆项必须只表达一个完整、连贯的意思。若一条消息包含多个 \
            独立想法，请拆成多条记忆项。要分别捕捉不同的事实主张，不要把无关事实混在 \
            一条里。
            2. 独立性：脱离上下文也能被单独检索理解。
            3. 内容保真：绝不能丢掉姓名、数字、参数名、版本号、技术术语、配置值、频率、 \
            地点或品牌等细节；只能删除填充词。
            4. 显式归因：必须明确写出是谁说了什么或做了什么，并把代词解析为具体名字。
            5. 仅限明确事实：只抽取直接陈述或明确确认的事实，不要猜测。
            """;

    private static final String EXTRACTION_SCOPE =
            """
            # Extraction Scope & What NOT to Extract
            - Extract from BOTH user AND assistant messages.
            - Extract only information that is likely to remain useful beyond the current \
            turn or immediate coordination context, such as over future days, weeks, or months.
            - Do not create a memory item just because a sentence is factual. Create a memory \
            only when it has durable retrieval value.
            - User messages mainly reveal profile, behavior, and event memories.
            - Keep assistant content ONLY when it contains durable agent instructions, \
            reusable task workflows, resolved problem knowledge, or concrete tool guidance \
            with future reuse value.
            - Do NOT extract: generic greetings, pure acknowledgements, filler and small talk, \
            process chatter, repeated facts in the same source text, temporary status updates \
            useful only inside the current session, one-off control messages, or vague \
            summaries without actionable or retrievable detail.
            - Do NOT extract assistant emotional support, encouragement, validation, reflective \
            coaching questions, or therapeutic phrasing unless the user explicitly adopts them \
            as a lasting routine, preference, or instruction.
            - For directive, playbook, and resolution items, normalize to reusable knowledge or \
            the durable rule itself.
            - Avoid conversational framing like "Assistant suggested:", "Assistant advised the \
            user to", or "At 2026-03-27 the assistant said...".
            - One-off control messages, transient execution commands, and session-management \
            turns are not durable agent memory.
            - A one-off command can still be extracted if it reveals a durable directive, \
            reusable playbook, project context, or resolution.
            - Temporary project status can still be extracted as event when it is important \
            context likely to matter later.
            """;

    private static final String EXTRACTION_BIAS =
            """
            # Extraction Bias
            - For USER-scope categories (`profile`, `behavior`, `event`), extract clearly \
            stated facts even if they seem minor, as long as they have future retrieval value.
            - For AGENT-scope categories (`tool`, `directive`, `playbook`, `resolution`), \
            use strict precision. If the item is merely a one-off command, loose suggestion, \
            unresolved discussion, or temporary execution status, do not extract it.
            - If there is no clear evidence, do not extract.
            - If uncertain between AGENT memory and nothing, prefer nothing.
            """;

    private static final String CONTENT_PRESERVATION =
            """
            # Content Preservation
            Preserve who, what, when, where, and why when those details affect future retrieval \
            or interpretation.
            Preserve important relationships, locations, causes, motivations, comparisons, \
            capabilities, attitudes, constraints, and user intent.
            Do not compress away the detail that makes the memory useful.
            Do not turn a specific memory into a vague summary.
            Prefer one complete, self-contained sentence over fragments.
            """;

    private static final String CORRECTION_RULES =
            """
            # Corrections & State Changes（重要）
            当源文本中出现对先前信息的更正、修改或推翻时（信号词如“更正”、“纠正”、\
            “不是A而是B”、“改为”、“调整为”、“之前说错了”），按以下规则处理：
            1. 最新状态照常抽取为独立的记忆项。
            2. 必须额外生成一条「变更记录」记忆项，把旧值一并保留：
               - 内容格式：“<对象><字段>发生更正：由「<旧值>」变更为「<新值>」”
               - 例：“2026年2月25日的拜访方式发生更正：由「电话沟通」变更为「现场拜访」”
               - 若同一字段发生多次更正，每次更正都要生成一条对应的变更记录。
            3. 绝不要只保留最新状态而丢弃旧值。“更正之前是什么”和“相比更正前有什么变化” \
            是常见的未来检索意图，缺少变更记录就无法回答这类问题。
            4. 变更记录通常归入 event；若被更正的对象有明确时间，`time` 使用变更发生的 \
            时间而非字段本身的业务时间。
            """;

    private static final String CATEGORY_CONTEXT_SECTION = "{{CATEGORY_CONTEXT}}";

    private static final String IDENTITY_CONTEXT_SECTION = "{{IDENTITY_CONTEXT}}";

    private static final String SUBJECT_CONTEXT_SECTION = "{{SUBJECT_CONTEXT}}";

    private static final String TEMPORAL_CONTEXT_SECTION = "{{TEMPORAL_CONTEXT}}";

    private static final String SCORING =
            """
            # Scoring Guidelines

            ## confidence
            0.0 to 1.0 indicating extraction certainty:
            - 0.95-1.0: Explicit direct statement
            - 0.85-0.94: Strong implication
            - 0.70-0.84: Reasonable inference
            - < 0.70: Do not extract

            ## time
            Temporal extraction rules for time-specific memories:
            - Use `time` as the primary temporal field. Set `time` to null when no semantic \
            temporal evidence exists in the source text.
            - `time.expression`: the original temporal phrase from the source text, such as \
            "昨天", "last week", or "March 2026".
            - `time.start`: the normalized lower bound in ISO-8601 UTC.
            - `time.end`: the normalized exclusive upper bound in ISO-8601 UTC for ranges and \
            calendar buckets; use null for a single point in time.
            - `time.granularity`: one of `point`, `day`, `week`, `month`, `year`, `range`, or \
            `unknown`.
            - Time-specific memories: embed the resolved absolute date or range in the content \
            AND populate `time` only when the text itself states or clearly implies that time.
            - Profile, behavior, directive, playbook, resolution, and tool items should \
            normally set `time` to null.
            - Event items should populate `time` only when the text itself contains explicit \
            temporal evidence such as a date, relative date phrase, or clear start/end marker.
            - Do NOT use message timestamps or conversation timestamps as default temporal \
            values. They are for resolving relative expressions, not persistence defaults.
            - For `day`, `week`, `month`, and `year`, `time.start` and `time.end` must form a \
            canonical half-open bucket in the System Time Zone before converting to UTC.
            - During rollout the parser still tolerates legacy `occurredAt`, but your response \
            should use `time`.
            {{GRAPH_HINT_RULES}}
            {{THREAD_SEMANTICS_RULES}}
            """;

    private static final String OUTPUT =
            """
            # OutputFormat
            仅返回 JSON 对象，不要有任何多余文本。
            当没有语义时间证据时使用 `"time": null`。
            {
              "items":[
                {
                  "content": "一句话，完整、自包含，保留全部细节",
                  "confidence": 0.95,
                  "time": {
                    "expression": "on 2023-10-14 at 08:30 UTC",
                    "start": "2023-10-14T08:30:00Z",
                    "end": null,
                    "granularity": "point"
                  },
                  "insightTypes": ["仅从所分配类别下列出的可用 insightTypes 中选择"],
            {{THREAD_SEMANTICS_OUTPUT_FIELDS}}
            {{GRAPH_OUTPUT_FIELDS}}
                  "category_reason": "关键：根据规则简要说明为什么选择该类别。此字段仅用于推理，不会被存储。",
                  "category": "<matched_category_from_list>"
                }
              ]
            }
            (没有符合条件的内容时返回 `{"items":[]}`)
            </OutputFormat>
            """;

    private static final String USER_PROMPT_TEMPLATE =
            """
            Please extract unified memory items from the following source text:

            <SourceText>
            {{CONVERSATION}}
            </SourceText>
            """;

    // ── Decision Logic & Common Confusions ────────────────────────────────────

    static final String DECISION_LOGIC =
            """
            # 类别判断

            ## 判断逻辑
            对每个抽取出的事实，判断它属于哪一种记忆。

            | Ask yourself                                                              | Answer points to        | Category   |
            |---------------------------------------------------------------------------|-------------------------|------------|
            | Is this about who the user is or an enduring preference or trait?         | Stable identity         | profile    |
            | Does the user do this repeatedly?                                         | Recurring pattern       | behavior   |
            | Is this a time-bound situation, current activity, or single occurrence?   | Time-bound situation    | event      |
            | Is the user setting a durable rule for how the agent should behave later? | Future interaction rule | directive  |
            | Is this a reusable workflow for handling a class of tasks?                | Repeatable method       | playbook   |
            | Is a named problem clearly resolved with a usable fix or conclusion?      | Problem plus resolution | resolution |
            | Does this describe how a specific tool was used or configured?            | Tool usage insight      | tool       |

            Match the narrowest valid category. Profile is the LAST resort, not the default.

            ## 常见混淆
            - "计划做 X" -> event（有时间边界的动作，不是 profile）
            - "项目 X 当前进展中" -> event（项目上下文，不是 profile）
            - "在做重大修改前先展示计划" -> directive
            - "回复中文并保持简洁" -> directive
            - "continue" -> 一次性控制消息，不要抽取。
            - "commit this" -> 临时执行命令。除非明确表达成长期协作规则，否则不要抽取。
            - "遇到问题 A，用 B 解决" -> resolution（不是 event）
            - "处理 X 的通用流程" -> playbook（不是 event）
            - "我们用 Redis/Kafka/X 做 Y" -> event（当前团队配置，不是 profile）
            - "当前在学习/阅读/迁移 X" -> event（进行中的活动，不是 profile）
            - "参数 X 配错导致问题 Y，已用 Z 修复" -> resolution
            - "X 不是 A，而是 B / 由 A 改为 B" -> 最新状态抽取为 event，\
            同时按 Corrections & State Changes 规则额外生成一条变更记录（含旧值）
            - "用户每天早上/每天晚上都会做 X" -> behavior（重复习惯，不是 event）
            - "某同事负责后端服务" -> event（团队上下文，不是 profile）
            - "不要在代码里加注释" -> directive（代理规则，不是 profile）
            - "做仓库比较时，先对齐范围，再比较分类、抽取流程和存储路径" -> playbook
            - "用户有固定 SOP" -> 如果是可复用的方法就算 playbook；如果只是个人习惯就算 behavior
            - "助手说‘温柔一点’，给出安慰或反思式提问" -> 不要抽取，除非用户后来把它变成自己的长期实践或指令
            - "助手给出明确配置修复或诊断结论" -> resolution（问题和可用修复都明确时）
            - "助手给出适用于一类任务的可复用步骤" -> playbook
            - "用户不喜欢冗长的代码注释" -> profile，除非明确是对代理行为的规则
            - 一般性行业/技术事实，如果不是专门描述用户自己的经历、决定或结果，则不要抽取
            """;

    // ── Category Definition Template ─────────────────────────────────────────

    static final String CATEGORY_DEF_TEMPLATE =
            """
            **{{CATEGORY_NAME}}**
            {{PROMPT_DEFINITION}}
            Available insightTypes: {{INSIGHT_TYPES}}
            """;

    // ── Per-Category Examples ─────────────────────────────────────────────────

    static final String CATEGORY_EXAMPLES =
            """

            # Examples by Category

            ## profile

            Good:
            {
              "items": [
                {
                  "content": "用户是一名有 5 年 Python 经验的后端工程师",
                  "confidence": 1.0,
                  "time": null,
                  "insightTypes": ["identity"],
                  "category_reason": "稳定的职业身份，在不同项目中都成立。",
                  "category": "profile"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User is building a Spring Boot 3 service on Java 21",
                  "category": "profile"
                }
              ]
            }
            -> Wrong: this is event. Current project context that becomes outdated when the project ends.


            ## behavior

            Good:
            {
              "items": [
                {
                  "content": "用户每个工作日都会在早会前审查 pull request",
                  "confidence": 1.0,
                  "time": null,
                  "insightTypes": ["behavior"],
                  "category_reason": "有明确频率证据的重复性习惯。",
                  "category": "behavior"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User went for a run yesterday",
                  "category": "behavior"
                }
              ]
            }
            -> Wrong: this is event. Single occurrence with no evidence of recurrence.


            ## event

            Good:
            {
              "items": [
                {
                  "content": "用户团队使用 Redis 做缓存，TTL 为 10 分钟",
                  "confidence": 0.95,
                  "time": null,
                  "insightTypes": ["experiences"],
                  "category_reason": "当前团队基础设施配置，没有具体时间锚点。",
                  "category": "event"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User is a Java developer",
                  "category": "event"
                }
              ]
            }
            -> Wrong: this is profile. Stable professional identity, not a time-bound situation.


            Correction example (event):
            {
              "items": [
                {
                  "content": "2026年2月25日的拜访方式发生更正：由「电话沟通」变更为「现场拜访」",
                  "confidence": 0.95,
                  "time": null,
                  "insightTypes": ["experiences"],
                  "category_reason": "对先前信息的更正，旧值与新值都必须保留以支持后续对比检索。",
                  "category": "event"
                },
                {
                  "content": "2026年2月25日的拜访方式为现场拜访",
                  "confidence": 0.95,
                  "time": {
                    "expression": "2026年2月25日",
                    "start": "2026-02-25T00:00:00Z",
                    "end": "2026-02-26T00:00:00Z",
                    "granularity": "day"
                  },
                  "insightTypes": ["experiences"],
                  "category_reason": "最新确认的状态事实。",
                  "category": "event"
                }
              ]
            }


            ## tool

            Good:
            {
              "items": [
                {
                  "content": "进行代码搜索时，使用带文件 glob 的 ripgrep 来缩小大型仓库的搜索范围",
                  "confidence": 0.95,
                  "time": null,
                  "insightTypes": ["tool_usage"],
                  "category_reason": "具体且可复用的工具使用建议，未来有复用价值。",
                  "category": "tool"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User asked the agent to run a search once",
                  "category": "tool"
                }
              ]
            }
            -> Wrong: a one-off tool command is not durable tool usage knowledge.


            ## directive

            Good:
            {
              "items": [
                {
                  "content": "用户要求在进行大规模代码修改前先展示计划",
                  "confidence": 1.0,
                  "time": null,
                  "insightTypes": ["directives"],
                  "category_reason": "面向未来交互的长期协作规则。",
                  "category": "directive"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "continue",
                  "category": "directive"
                }
              ]
            }
            -> Wrong: one-off control messages are not durable instructions.

            ## playbook

            Good:
            {
              "items": [
                {
                  "content": "在比较仓库时，先对齐记忆范围，再比较分类体系、抽取流程和存储路径",
                  "confidence": 0.95,
                  "time": null,
                  "insightTypes": ["playbooks"],
                  "category_reason": "适用于重复场景的可复用工作流程。",
                  "category": "playbook"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User asked to compare two repositories",
                  "category": "playbook"
                }
              ]
            }
            -> Wrong: a single request title is not a reusable workflow.

            ## resolution

            Good:
            {
              "items": [
                {
                  "content": "虚拟线程导致 HikariCP 连接池耗尽，因为虚拟线程数量超过了连接池大小；通过将 maximumPoolSize 设为 10 到 20 解决",
                  "confidence": 0.95,
                  "time": null,
                  "insightTypes": ["resolutions"],
                  "category_reason": "包含明确问题和可复用修复方案的解决记录。",
                  "category": "resolution"
                }
              ]
            }

            Bad:
            {
              "items": [
                {
                  "content": "User had connection pool issues with HikariCP",
                  "category": "resolution"
                }
              ]
            }
            -> Wrong: a resolution must include both the problem and a usable fix or conclusion.

            Bad:
            {
              "items": [
                {
                  "content": "助手建议用户温和地提醒自己：‘我在学习慢慢回到状态，不必着急’",
                  "category": "directive"
                }
              ]
            }
            -> Wrong: assistant emotional support or reflective guidance is not durable agent \
            memory unless the user later adopts it as their own routine or instruction.
            """;

    // ── Constructor ──────────────────────────────────────────────────────────

    private MemoryItemUnifiedPrompts() {}

    // ── Public API ───────────────────────────────────────────────────────────

    public static PromptTemplate build(
            List<MemoryInsightType> insightTypes,
            String segmentText,
            Instant referenceTime,
            String userName,
            Set<MemoryCategory> categories) {
        return build(
                insightTypes,
                segmentText,
                referenceTime,
                userName,
                categories,
                ItemGraphOptions.defaults());
    }

    public static PromptTemplate build(
            List<MemoryInsightType> insightTypes,
            String segmentText,
            Instant referenceTime,
            String userName,
            Set<MemoryCategory> categories,
            ItemGraphOptions graphOptions) {
        return build(
                PromptRegistry.EMPTY,
                insightTypes,
                segmentText,
                referenceTime,
                userName,
                categories,
                graphOptions);
    }

    public static PromptTemplate buildDefault() {
        return defaultBuilder().build();
    }

    public static PromptTemplate buildPreview() {
        return defaultBuilder()
                .variable("CATEGORY_CONTEXT", buildCategoryContext(null, DefaultInsightTypes.all()))
                .variable("CATEGORY_EXAMPLES", buildCategoryExamples(null))
                .variable("IDENTITY_CONTEXT", buildIdentityContext("Ada"))
                .variable("SUBJECT_CONTEXT", buildSubjectClarityContext("Ada"))
                .variable(
                        "TEMPORAL_CONTEXT",
                        buildTimeContext(null, Instant.parse("2026-03-29T00:00:00Z")))
                .build();
    }

    public static PromptTemplate build(
            PromptRegistry registry,
            List<MemoryInsightType> insightTypes,
            String segmentText,
            Instant referenceTime,
            String userName,
            Set<MemoryCategory> categories) {
        return build(
                registry,
                insightTypes,
                segmentText,
                referenceTime,
                userName,
                categories,
                ItemGraphOptions.defaults());
    }

    public static PromptTemplate build(
            PromptRegistry registry,
            List<MemoryInsightType> insightTypes,
            String segmentText,
            Instant referenceTime,
            String userName,
            Set<MemoryCategory> categories,
            ItemGraphOptions graphOptions) {

        PromptTemplate.Builder builder =
                registry.hasOverride(PromptType.MEMORY_ITEM_UNIFIED)
                        ? PromptTemplate.builder("memory-item-unified")
                                .section(
                                        "system",
                                        registry.getOverride(PromptType.MEMORY_ITEM_UNIFIED))
                        : defaultBuilder();

        return builder.userPrompt(USER_PROMPT_TEMPLATE)
                .variable("CATEGORY_CONTEXT", buildCategoryContext(categories, insightTypes))
                .variable("CATEGORY_EXAMPLES", buildCategoryExamples(categories))
                .variable("IDENTITY_CONTEXT", buildIdentityContext(userName))
                .variable("SUBJECT_CONTEXT", buildSubjectClarityContext(userName))
                .variable("TEMPORAL_CONTEXT", buildTimeContext(segmentText, referenceTime))
                .variable("GRAPH_HINT_RULES", buildGraphHintRules(graphOptions))
                .variable("THREAD_SEMANTICS_RULES", buildThreadSemanticsRules())
                .variable("THREAD_SEMANTICS_OUTPUT_FIELDS", buildThreadSemanticsOutputFields())
                .variable("GRAPH_OUTPUT_FIELDS", buildGraphOutputFields(graphOptions))
                .variable("CONVERSATION", segmentText != null ? segmentText : "")
                .build();
    }

    private static String buildThreadSemanticsRules() {
        return """

        ## threadSemantics
        - Optional. Emit only when the item clearly contains durable thread-relevant meaning.
        - If emitted, `threadSemantics.version` must be `1`.
        - Use `threadSemantics.markers` for strong semantic events such as `STATE_CHANGE`, `BLOCKER_ADDED`, `DECISION_MADE`, `QUESTION_OPENED`, `MILESTONE_REACHED`, or `RESOLUTION_DECLARED`.
        - Use `threadSemantics.canonicalRefs` for typed stable refs like `project`, `person`, `organization`, or `topic`.
        - Use `threadSemantics.continuityLinks` only for explicit continuation evidence pointing to an earlier item.
        - If unsure, omit `threadSemantics` entirely.
        """;
    }

    private static String buildGraphHintRules(ItemGraphOptions graphOptions) {
        if (graphOptions == null || !graphOptions.enabled()) {
            return "";
        }
        return """

        ## Graph Hints
        - Include `"entities"` only for concrete, high-value named entities; keep at most %d per item.
        - Allowed `"entityType"` values are `person`, `organization`, `place`, `object`, `concept`, and `special`.
        - Use "special" only for conversational role anchors such as self, user, or assistant; do not label arbitrary nouns as special.
        - Good entities: named people, organizations, places, durable objects, and durable concepts central to the item.
        - Bad entities: pronouns, generic nouns, dates, categories, vague topics, or entities not grounded in the source.
        - Extract entities only when they help link related memories. Prefer concrete named entities and specific durable domain concepts over generic nouns.
        - Resolve role-only mentions to named entities when the source provides a name.
        - If "my roommate" and "Emily" refer to the same person, use entity name "Emily" and preserve "user's roommate" in content or alias evidence.
        - If "the PM" and "Sarah" refer to the same person, use entity name "Sarah" and preserve "PM" as role evidence.
        - Do not create separate entities for generic role mentions when a named entity is available.
        - Do not extract generic nouns such as "project", "team", "system", "issue", or "problem" unless grounded as a specific named or durable domain concept.
        - Include `"causalRelations"` only for strong explicit cause/effect links inside this response; keep at most %d per item.
        - Good causal relation: one item states a cause, trigger, enabler, or motivation for another item.
        - Bad causal relation: not for topical similarity, not for ownership or dependency, and not for simple co-occurrence.
        - `"entities"` uses objects with `"name"`, `"entityType"`, optional `"salience"`, and optional `"aliasObservations"`.
        - Each `"aliasObservations"` entry uses `"aliasSurface"`, `"aliasClass"`, optional `"evidenceSource"`, and optional `"confidence"`.
        - Allowed `"aliasClass"` values are `case_only`, `punctuation`, `spacing`, `org_suffix`, `explicit_parenthetical`, `explicit_slash_apposition`, and `user_dictionary`.
        - `"causalRelations"` uses objects with `"causeIndex"`, `"effectIndex"`, `"relationType"`, and `"strength"` in [0,1].
        - If you emit a causal relation, include an explicit strength in [0,1]; otherwise omit the relation.
        - causeIndex and effectIndex must reference different items in the same response.
        - Prefer omission to hallucinated graph structure.
        """
                .formatted(
                        graphOptions.maxEntitiesPerItem(),
                        graphOptions.maxCausalReferencesPerItem());
    }

    private static String buildThreadSemanticsOutputFields() {
        return "      \"threadSemantics\": {\n"
                + "        \"version\": 1,\n"
                + "        \"markers\": [\n"
                + "          {\n"
                + "            \"type\": \"STATE_CHANGE\",\n"
                + "            \"objectRef\": \"project:alpha\",\n"
                + "            \"summary\": \"Project alpha moved into implementation\",\n"
                + "            \"fromState\": \"planning\",\n"
                + "            \"toState\": \"implementation\"\n"
                + "          }\n"
                + "        ],\n"
                + "        \"canonicalRefs\": [\n"
                + "          {\n"
                + "            \"refType\": \"project\",\n"
                + "            \"refKey\": \"alpha\"\n"
                + "          }\n"
                + "        ],\n"
                + "        \"continuityLinks\": [\n"
                + "          {\n"
                + "            \"linkType\": \"CONTINUES\",\n"
                + "            \"targetItemId\": 0\n"
                + "          }\n"
                + "        ]\n"
                + "      },\n";
    }

    private static String buildGraphOutputFields(ItemGraphOptions graphOptions) {
        if (graphOptions == null || !graphOptions.enabled()) {
            return "";
        }
        return "      \"entities\": [\n"
                + "        {\n"
                + "          \"name\": \"OpenAI\",\n"
                + "          \"entityType\": \"organization\",\n"
                + "          \"salience\": 0.91,\n"
                + "          \"aliasObservations\": [\n"
                + "            {\n"
                + "              \"aliasSurface\": \"开放人工智能\",\n"
                + "              \"aliasClass\": \"explicit_parenthetical\",\n"
                + "              \"evidenceSource\": \"entity_inline\",\n"
                + "              \"confidence\": 0.93\n"
                + "            }\n"
                + "          ]\n"
                + "        }\n"
                + "      ],\n"
                + "      \"causalRelations\": [\n"
                + "        {\n"
                + "          \"causeIndex\": 0,\n"
                + "          \"effectIndex\": 1,\n"
                + "          \"relationType\": \"caused_by\",\n"
                + "          \"strength\": 0.88\n"
                + "        }\n"
                + "      ],\n";
    }

    private static PromptTemplate.Builder defaultBuilder() {
        return PromptTemplate.builder("memory-item-unified")
                .section("objective", OBJECTIVE)
                .section("principles", PRINCIPLES)
                .section("extractionScope", EXTRACTION_SCOPE)
                .section("extractionBias", EXTRACTION_BIAS)
                .section("contentPreservation", CONTENT_PRESERVATION)
                .section("correctionRules", CORRECTION_RULES)
                .section("outputLanguage", PromptLanguageRules.MATCH_SOURCE_LANGUAGE)
                .section("categoryContext", CATEGORY_CONTEXT_SECTION)
                .section("identityContext", IDENTITY_CONTEXT_SECTION)
                .section("subjectContext", SUBJECT_CONTEXT_SECTION)
                .section("temporalContext", TEMPORAL_CONTEXT_SECTION)
                .section("scoring", SCORING)
                .section("output", OUTPUT)
                .section("examples", "{{CATEGORY_EXAMPLES}}");
    }

    // ── Category Context ─────────────────────────────────────────────────────

    static String buildCategoryExamples(Set<MemoryCategory> categories) {
        Set<MemoryCategory> effectiveCategories = effectiveCategories(categories);
        if (effectiveCategories.isEmpty()) {
            return "";
        }
        String examples = renderExamplesFor(effectiveCategories);
        return examples.isBlank() ? "" : "\n\n# Examples by Category\n" + examples;
    }

    private static Set<MemoryCategory> effectiveCategories(Set<MemoryCategory> categories) {
        if (categories == null) {
            return EnumSet.allOf(MemoryCategory.class);
        }
        if (categories.isEmpty()) {
            return EnumSet.noneOf(MemoryCategory.class);
        }
        return EnumSet.copyOf(categories);
    }

    private static String renderExamplesFor(Set<MemoryCategory> categories) {
        return categories.stream()
                .map(MemoryCategory::categoryName)
                .map(MemoryItemUnifiedPrompts::renderExampleFor)
                .filter(example -> !example.isBlank())
                .collect(Collectors.joining());
    }

    private static String renderExampleFor(String categoryName) {
        String heading = "\n## " + categoryName + "\n";
        int start = CATEGORY_EXAMPLES.indexOf(heading);
        if (start < 0) {
            return "";
        }
        int next = CATEGORY_EXAMPLES.indexOf("\n## ", start + heading.length());
        int end = next < 0 ? CATEGORY_EXAMPLES.length() : next;
        return CATEGORY_EXAMPLES.substring(start, end);
    }

    static String buildCategoryContext(
            Set<MemoryCategory> categories, List<MemoryInsightType> insightTypes) {
        Set<MemoryCategory> effectiveCategories = effectiveCategories(categories);

        String userDefs =
                renderCategoryDefinitions(effectiveCategories, MemoryScope.USER, insightTypes);
        String agentDefs =
                renderCategoryDefinitions(effectiveCategories, MemoryScope.AGENT, insightTypes);

        String defs =
                (userDefs.isEmpty() ? "" : "### [USER Scope]\n\n" + userDefs)
                        + (agentDefs.isEmpty() ? "" : "### [AGENT Scope]\n\n" + agentDefs);

        return DECISION_LOGIC + "\n## Category Definitions\n\n" + defs;
    }

    private static String renderCategoryDefinitions(
            Set<MemoryCategory> categories,
            MemoryScope scope,
            List<MemoryInsightType> insightTypes) {
        return categories.stream()
                .filter(cat -> cat.scope() == scope)
                .map(cat -> renderSingleCategory(cat, insightTypes))
                .collect(Collectors.joining());
    }

    private static String renderSingleCategory(
            MemoryCategory cat, List<MemoryInsightType> insightTypes) {
        String matchedTypes =
                insightTypes == null
                        ? "none"
                        : insightTypes.stream()
                                .filter(
                                        it ->
                                                it.categories() != null
                                                        && it.categories()
                                                                .contains(cat.categoryName()))
                                .map(MemoryInsightType::name)
                                .collect(Collectors.joining(", "));

        return CATEGORY_DEF_TEMPLATE
                .replace("{{CATEGORY_NAME}}", cat.categoryName())
                .replace("{{PROMPT_DEFINITION}}", cat.promptDefinition())
                .replace("{{INSIGHT_TYPES}}", matchedTypes.isEmpty() ? "none" : matchedTypes);
    }

    // ── Identity Context ─────────────────────────────────────────────────────

    static final String IDENTITY_WITH_NAME =
            """
            # Identity
            真实姓名优先：如果用户提供了真实姓名，请在所有抽取项中使用真实姓名替代“User”或“用户”作为主体。
            (e.g., "{{USER_NAME}} likes..." NOT "User likes...")
            """;

    static final String IDENTITY_DEFAULT =
            """
            # Identity
            Use "User" to refer to the user consistently in all extracted items.
            """;

    static final String SUBJECT_CLARITY_TEMPLATE =
            """
            # Subject Clarity
            Every memory item must be understandable without the original conversation.
            "{{OWNER_LABEL}}" refers only to the memory owner.
            If the item is about someone other than {{OWNER_LABEL}}, explicitly name that subject \
            with a stable role phrase, such as "{{OWNER_LABEL}}的朋友", "朋友的继子", or \
            "{{OWNER_LABEL}}的同事们".
            Do NOT use bare pronouns like "他", "她", "他们", or "自己" when the referent is \
            not unmistakably clear from the same sentence.
            Prefer repeating explicit role phrases over ambiguous pronouns.
            If the subject cannot be made explicit from the source text, do not extract the item.
            """;

    static String buildIdentityContext(String userName) {
        if (userName == null || userName.isBlank()) {
            return IDENTITY_DEFAULT;
        }
        return IDENTITY_WITH_NAME.replace("{{USER_NAME}}", userName);
    }

    static String buildSubjectClarityContext(String userName) {
        String ownerLabel = (userName == null || userName.isBlank()) ? "User" : userName;
        return SUBJECT_CLARITY_TEMPLATE.replace("{{OWNER_LABEL}}", ownerLabel);
    }

    // ── Temporal Context ─────────────────────────────────────────────────────

    static String buildTimeContext(String segmentText, Instant referenceTime) {
        boolean hasTimestamps =
                segmentText != null && MESSAGE_TIMESTAMP_PATTERN.matcher(segmentText).find();
        ZoneId systemZone = ZoneId.systemDefault();
        String zoneLine = "System Time Zone: " + systemZone.getId() + "\n";
        String bucketRule =
                "For `day`, `week`, `month`, and `year`, `time.start` and `time.end` must"
                        + " form canonical half-open bucket boundaries in this system time"
                        + " zone before converting to UTC.\n";

        if (hasTimestamps) {
            String fallback =
                    referenceTime != null
                            ? "\nFallback Reference Date: "
                                    + formatReferenceDate(referenceTime, systemZone)
                            : "";
            return "# Temporal Resolution\n"
                    + zoneLine
                    + "Messages contain timestamps (e.g., [2023-05-25 13:17]). Use each message's"
                    + " timestamp only as a reference anchor for resolving relative expressions"
                    + " within that message. Do NOT copy message timestamps into `time` or"
                    + " legacy `occurredAt` unless the memory text itself makes that time"
                    + " semantically explicit."
                    + fallback
                    + "\n\n"
                    + bucketRule
                    + "\n"
                    + RESOLVE_DATES_INSTRUCTION
                    + "\n";
        } else if (referenceTime != null) {
            return "# Temporal Resolution\n"
                    + zoneLine
                    + "Today's date: "
                    + formatReferenceDate(referenceTime, systemZone)
                    + ". Use this only to resolve relative temporal references. Do NOT treat it"
                    + " as a default temporal value.\n\n"
                    + bucketRule
                    + "\n"
                    + RESOLVE_DATES_INSTRUCTION
                    + "\n";
        } else {
            return """
            # Temporal Resolution
            System Time Zone:\
            """
                    + systemZone.getId()
                    + """

                    No absolute dates available. Do not resolve relative dates.
                    For `day`, `week`, `month`, and `year`, `time.start` and `time.end` must form canonical \
                    half-open bucket boundaries in this system time zone before converting to UTC.
                    """;
        }
    }

    private static String formatReferenceDate(Instant referenceTime, ZoneId systemZone) {
        return referenceTime.atZone(systemZone).toLocalDate().format(DATE_FMT);
    }
}
