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
package com.openmemind.ai.memory.core.retrieval.scoring;

import static org.assertj.core.api.Assertions.assertThat;

import com.openmemind.ai.memory.core.data.DefaultMemoryId;
import com.openmemind.ai.memory.core.retrieval.query.QueryContext;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

@DisplayName("QueryAwareResultPrioritizer Unit Test")
class QueryAwareResultPrioritizerTest {

    @Test
    @DisplayName("Current or corrected queries should prefer the latest explicit answer")
    void shouldPreferLatestExplicitAnswerForCurrentStyleQuery() {
        var context =
                new QueryContext(
                        DefaultMemoryId.of("mem-1", "agent-1"),
                        "更正之后，研发贷的当前利率是多少？",
                        null,
                        List.of(),
                        Map.of(),
                        null,
                        null);

        var older =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "old", "研发贷更正前为LPR下浮30个基点", 0.7f, 0.7);
        var newer =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "new", "研发贷更正后为LPR下浮50个基点", 0.7f, 0.7);

        List<ScoredResult> result =
                QueryAwareResultPrioritizer.prioritize(List.of(older, newer), context);

        assertThat(result).hasSize(2);
        assertThat(result.get(0).sourceId()).isEqualTo("new");
    }

    @Test
    @DisplayName(
            "Year-specific queries should prefer matching year evidence and penalize mismatched"
                    + " years")
    void shouldPreferMatchingYearEvidence() {
        var context =
                new QueryContext(
                        DefaultMemoryId.of("mem-1", "agent-1"),
                        "鑫科精密零部件制造有限公司在2024年的授信额度是多少？",
                        null,
                        List.of(),
                        Map.of(),
                        null,
                        null);

        var matchedYear =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "matched", "2024年授信额度为6000万元", 0.7f, 0.7);
        var mismatchedYear =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "mismatched", "2025年授信额度为6000万元", 0.7f, 0.7);

        List<ScoredResult> result =
                QueryAwareResultPrioritizer.prioritize(
                        List.of(matchedYear, mismatchedYear), context);

        assertThat(result).hasSize(2);
        assertThat(result.get(0).sourceId()).isEqualTo("matched");
    }

    @Test
    @DisplayName("Cause style queries should prefer explanatory conclusions over loose facts")
    void shouldPreferCausalExplanations() {
        var context =
                new QueryContext(
                        DefaultMemoryId.of("mem-1", "agent-1"),
                        "抵押物复评贬值对授信额度有何影响？",
                        null,
                        List.of(),
                        Map.of(),
                        null,
                        null);

        var factOnly =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "fact", "抵押物评估价为4800万元，抵押手续已完成", 0.7f, 0.7);
        var causeAnswer =
                new ScoredResult(
                        ScoredResult.SourceType.ITEM, "cause", "抵押物复评贬值会影响授信额度", 0.7f, 0.7);

        List<ScoredResult> result =
                QueryAwareResultPrioritizer.prioritize(List.of(factOnly, causeAnswer), context);

        assertThat(result).hasSize(2);
        assertThat(result.get(0).sourceId()).isEqualTo("cause");
    }
}
