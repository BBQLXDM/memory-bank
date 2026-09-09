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
package com.openmemind.ai.memory.plugin.jdbc.sqlite;

import static org.assertj.core.api.Assertions.assertThat;

import com.openmemind.ai.memory.core.extraction.rawdata.content.conversation.message.Message;
import com.openmemind.ai.memory.plugin.jdbc.internal.buffer.ConversationBufferRow;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.BrokenBarrierException;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.sqlite.SQLiteDataSource;

class SqliteConversationBufferTest {

    @Test
    void pendingAndRecentViewsShareOnePersistedConversationLog(@TempDir Path tempDir) {
        SQLiteDataSource dataSource = new SQLiteDataSource();
        dataSource.setUrl("jdbc:sqlite:" + tempDir.resolve("conversation.db"));

        var pendingBuffer = new SqliteConversationBuffer(dataSource, true);
        var recentBuffer = new SqliteRecentConversationBuffer(dataSource, true);
        String sessionId = "user-1:agent-1";

        pendingBuffer.append(
                sessionId, Message.user("hello", Instant.parse("2026-04-01T00:00:00Z")));
        pendingBuffer.append(
                sessionId, Message.assistant("hi", Instant.parse("2026-04-01T00:00:01Z")));

        assertThat(pendingBuffer.load(sessionId))
                .extracting(Message::textContent)
                .containsExactly("hello", "hi");
        assertThat(recentBuffer.loadRecent(sessionId, 10))
                .extracting(Message::textContent)
                .containsExactly("hello", "hi");

        assertThat(pendingBuffer.drain(sessionId))
                .extracting(Message::textContent)
                .containsExactly("hello", "hi");

        assertThat(pendingBuffer.load(sessionId)).isEmpty();
        assertThat(recentBuffer.loadRecent(sessionId, 10))
                .extracting(Message::textContent)
                .containsExactly("hello", "hi");
    }

    @Test
    void concurrentDrainClaimsPreexistingBatchOnce(@TempDir Path tempDir) throws Exception {
        SQLiteDataSource dataSource = sqliteDataSource(tempDir.resolve("concurrent.db"));
        String sessionId = "sqlite-concurrent-user:agent";
        int messageCount = 20;
        SqliteConversationBuffer seedBuffer = new SqliteConversationBuffer(dataSource);
        for (int i = 0; i < messageCount; i++) {
            seedBuffer.append(
                    sessionId,
                    Message.user(
                            "message-" + i, Instant.parse("2026-04-01T00:00:00Z").plusSeconds(i)));
        }

        CyclicBarrier afterBothSelections = new CyclicBarrier(2);
        // Regression trap for the old select-then-mark drain path.
        var first =
                new SqliteConversationBuffer(
                        new BlockingSelectAccessor(dataSource, afterBothSelections));
        var second =
                new SqliteConversationBuffer(
                        new BlockingSelectAccessor(dataSource, afterBothSelections));

        List<List<Message>> drained = drainConcurrently(first, second, sessionId);

        assertAtomicDrainResult(drained, messageCount);
        assertThat(seedBuffer.load(sessionId)).isEmpty();
        assertThat(new SqliteRecentConversationBuffer(dataSource).loadRecent(sessionId, 100))
                .extracting(Message::textContent)
                .containsExactlyElementsOf(expectedMessages(messageCount));
    }

    private static List<List<Message>> drainConcurrently(
            SqliteConversationBuffer first, SqliteConversationBuffer second, String sessionId)
            throws Exception {
        ExecutorService executor = Executors.newFixedThreadPool(2);
        CyclicBarrier startBarrier = new CyclicBarrier(2);
        try {
            Callable<List<Message>> firstDrain =
                    () -> drainAfterBarrier(first, sessionId, startBarrier);
            Callable<List<Message>> secondDrain =
                    () -> drainAfterBarrier(second, sessionId, startBarrier);
            Future<List<Message>> firstResult = executor.submit(firstDrain);
            Future<List<Message>> secondResult = executor.submit(secondDrain);
            return List.of(firstResult.get(), secondResult.get());
        } finally {
            executor.shutdownNow();
        }
    }

    private static List<Message> drainAfterBarrier(
            SqliteConversationBuffer buffer, String sessionId, CyclicBarrier startBarrier) {
        awaitBarrier(startBarrier);
        return buffer.drain(sessionId);
    }

    private static void assertAtomicDrainResult(List<List<Message>> drained, int messageCount) {
        List<Message> combined = drained.stream().flatMap(List::stream).toList();

        assertThat(combined).hasSize(messageCount);
        assertThat(combined)
                .extracting(Message::textContent)
                .containsExactlyInAnyOrderElementsOf(expectedMessages(messageCount));
        assertThat(drained.stream().map(List::size).sorted().toList())
                .containsExactly(0, messageCount);
    }

    private static List<String> expectedMessages(int messageCount) {
        List<String> expected = new ArrayList<>(messageCount);
        for (int i = 0; i < messageCount; i++) {
            expected.add("message-" + i);
        }
        return expected;
    }

    private static SQLiteDataSource sqliteDataSource(Path path) {
        SQLiteDataSource dataSource = new SQLiteDataSource();
        dataSource.setUrl("jdbc:sqlite:" + path + "?busy_timeout=5000");
        return dataSource;
    }

    private static final class BlockingSelectAccessor extends SqliteConversationBufferAccessor {

        private final CyclicBarrier afterBothSelections;

        private BlockingSelectAccessor(DataSource dataSource, CyclicBarrier afterBothSelections) {
            super(dataSource);
            this.afterBothSelections = afterBothSelections;
        }

        @Override
        public List<ConversationBufferRow> selectPending(String sessionId) {
            List<ConversationBufferRow> rows = super.selectPending(sessionId);
            if (rows.isEmpty()) {
                return rows;
            }
            awaitBothSelections();
            return rows.stream()
                    .sorted(Comparator.comparingLong(ConversationBufferRow::id))
                    .toList();
        }

        private void awaitBothSelections() {
            awaitBarrier(afterBothSelections);
        }
    }

    private static void awaitBarrier(CyclicBarrier barrier) {
        try {
            barrier.await();
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while coordinating drain race", ex);
        } catch (BrokenBarrierException ex) {
            throw new IllegalStateException("Failed to coordinate drain race", ex);
        }
    }
}
