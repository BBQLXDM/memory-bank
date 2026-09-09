# Source-language memory generation change notes

## Goal

Make model-generated human-readable memory content follow the primary language of the source content:

- Chinese source content produces Chinese captions and memory items.
- English source content produces English captions and memory items.
- Mixed-language content uses the primary narrative language while preserving technical terms.
- JSON field names, enum values, category identifiers, and schema constants remain unchanged.

## Observed problem

Chinese conversation input currently produces English Raw Data captions and English memory Items.

The first attempted change added a source-language instruction to:

- `CaptionPrompts`
- `MemoryItemUnifiedPrompts`

Runtime verification showed this was insufficient. The final prompt also receives a stronger rule from `PromptResult`:

```text
# ABSOLUTE REQUIREMENT: Output Language = English
```

This conflicts with the source-language instruction and causes the model to continue producing English.

## First prompt-level changes retained in the working tree

A reusable rule was added:

```text
memind-core/src/main/java/com/openmemind/ai/memory/core/prompt/PromptLanguageRules.java
```

It is integrated into:

```text
memind-core/src/main/java/com/openmemind/ai/memory/core/prompt/extraction/rawdata/CaptionPrompts.java
memind-core/src/main/java/com/openmemind/ai/memory/core/prompt/extraction/item/MemoryItemUnifiedPrompts.java
```

Related tests were updated to include the `outputLanguage` named section and verify that the source-language instruction is present.

These changes compile and their focused tests passed before the later SOURCE-mode experiment.

## Temporarily reverted SOURCE-mode experiment

The following experimental changes were reverted so the project returns to its prior compilable behavior:

- Removed `PromptResult.SOURCE_LANGUAGE`.
- Removed the special SOURCE branch from `PromptResult.languageRule`.
- Restored `LlmConversationCaptionGenerator` to `render(language)`.
- Removed the SOURCE-mode assertions from `PromptPreviewTest`.

The experiment had not yet been completed for the Memory Item production call chain and had triggered a Spotless formatting failure in its new test assertion.

## Recommended continuation

1. Locate where the production extraction pipeline selects or defaults the `language` argument for Caption and Memory Item generation.
2. Add a formally supported source-language/auto mode in `PromptResult`.
3. Route only Caption and Memory Item generation through that mode initially.
4. Add tests asserting that the final assembled prompt:
   - contains the source-language absolute requirement;
   - does not contain `Output Language = English` in source mode;
   - preserves existing fixed `English` and `Chinese` behavior.
5. Locate and update the Memory Item production render call, not only its prompt builder.
6. Run Spotless and focused tests.
7. Build/install modules and restart the backend.
8. Verify with separate Chinese and English test workspaces.

## Runtime acceptance criteria

Chinese input:

```text
我主要使用 Java 和 Spring Boot 开发后端服务。
```

Expected:

- Chinese Raw Data caption
- Chinese memory Items

English input:

```text
I mainly use Java and Spring Boot for backend development.
```

Expected:

- English Raw Data caption
- English memory Items

For both cases, schema keys and enum/category values must remain unchanged and retrieval must continue to work.
