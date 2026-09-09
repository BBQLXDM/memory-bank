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
package com.openmemind.ai.memory.core.prompt;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

@DisplayName("PromptResult")
class PromptResultTest {

    @Test
    @DisplayName("languageRule should emit source-language requirement for SOURCE mode")
    void sourceModeEmitsSourceLanguageRule() {
        String rule = PromptResult.languageRule(PromptResult.SOURCE_LANGUAGE);

        assertThat(rule).contains("# 绝对要求：输出语言 = 源文本语言");
        assertThat(rule).contains("检测源文本的主要语言");
        assertThat(rule).contains("如果源文本是中文");
        assertThat(rule).doesNotContain("Output Language = English");
    }

    @Test
    @DisplayName("of() with SOURCE_LANGUAGE should not inject fixed English requirement")
    void sourceModePromptDoesNotContainEnglishRule() {
        PromptResult result =
                PromptResult.of(
                        "Extract memory items.", "User content", PromptResult.SOURCE_LANGUAGE);

        assertThat(result.systemPrompt()).contains("# 绝对要求：输出语言 = 源文本语言");
        assertThat(result.systemPrompt()).doesNotContain("Output Language = English");
    }

    @Test
    @DisplayName("fixed languages keep existing behavior")
    void fixedLanguageKeepsExistingBehavior() {
        PromptResult english = PromptResult.of("Instruction.", "Content", "English");
        assertThat(english.systemPrompt()).contains("Output Language = English");

        PromptResult chinese = PromptResult.of("Instruction.", "Content", "Chinese");
        assertThat(chinese.systemPrompt()).contains("Output Language = Chinese");
        assertThat(chinese.systemPrompt()).doesNotContain("Output Language = English");
    }
}
