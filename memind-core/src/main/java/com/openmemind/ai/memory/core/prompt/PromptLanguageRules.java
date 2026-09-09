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

public final class PromptLanguageRules {

    public static final String MATCH_SOURCE_LANGUAGE =
            """
            # 输出语言
            - 检测源文本的主要语言。
            - 用该语言书写所有人类可读的生成值。
            - 除非明确要求翻译，否则不要翻译源文本。
            - 保留专有名词、产品名、API 名、代码标识符、文件路径、配置键和技术术语。
            - 保持 JSON 字段名、枚举值、类别标识符和 schema 定义的常量完全不变。
            - 对混合语言内容，使用主要叙述语言，同时保留其他语言的嵌入术语。
            - 如果源文本是中文，所有输出字段（content、caption、evidence、category_reason 等）必须用中文书写。
            - 如果源文本是英文，所有输出字段必须用英文书写。
            """;

    private PromptLanguageRules() {}
}
