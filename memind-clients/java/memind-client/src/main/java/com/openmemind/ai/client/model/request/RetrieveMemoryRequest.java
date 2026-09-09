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
package com.openmemind.ai.client.model.request;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.openmemind.ai.client.model.common.Strategy;
import java.time.Instant;
import java.util.List;
import java.util.Objects;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record RetrieveMemoryRequest(
        String userId,
        String agentId,
        String query,
        Strategy strategy,
        Boolean trace,
        String scope,
        List<String> categories,
        TimeRange timeRange,
        MetadataFilter metadataFilter,
        IncludeOptions include) {

    public static Builder builder() {
        return new Builder();
    }

    public RetrieveMemoryRequest(
            String userId, String agentId, String query, Strategy strategy, Boolean trace) {
        this(userId, agentId, query, strategy, trace, null, null, null, null, null);
    }

    public record TimeRange(String field, Instant from, Instant to) {}

    public record IncludeOptions(Boolean rawDataMetadata, Boolean rawDataSegment) {}

    public static final class Builder {

        private String userId;
        private String agentId;
        private String query;
        private Strategy strategy;
        private Boolean trace;
        private String scope;
        private List<String> categories;
        private TimeRange timeRange;
        private MetadataFilter metadataFilter;
        private IncludeOptions include;

        public Builder userId(String userId) {
            this.userId = userId;
            return this;
        }

        public Builder agentId(String agentId) {
            this.agentId = agentId;
            return this;
        }

        public Builder query(String query) {
            this.query = query;
            return this;
        }

        public Builder strategy(Strategy strategy) {
            this.strategy = strategy;
            return this;
        }

        public Builder trace(Boolean trace) {
            this.trace = trace;
            return this;
        }

        public Builder scope(String scope) {
            this.scope = scope;
            return this;
        }

        public Builder categories(List<String> categories) {
            this.categories = categories;
            return this;
        }

        public Builder timeRange(TimeRange timeRange) {
            this.timeRange = timeRange;
            return this;
        }

        public Builder metadataFilter(MetadataFilter metadataFilter) {
            this.metadataFilter = metadataFilter;
            return this;
        }

        public Builder include(IncludeOptions include) {
            this.include = include;
            return this;
        }

        public RetrieveMemoryRequest build() {
            Objects.requireNonNull(userId, "userId");
            Objects.requireNonNull(agentId, "agentId");
            Objects.requireNonNull(query, "query");
            Objects.requireNonNull(strategy, "strategy");
            return new RetrieveMemoryRequest(
                    userId,
                    agentId,
                    query,
                    strategy,
                    trace,
                    scope,
                    categories,
                    timeRange,
                    metadataFilter,
                    include);
        }
    }
}
