ALTER TABLE quiz_attempts
    ADD COLUMN active_attempt TINYINT GENERATED ALWAYS AS (
        CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE NULL END
    ) STORED,
    ADD CONSTRAINT uq_quiz_attempts_active UNIQUE (quiz_id, student_id, active_attempt),
    ADD CONSTRAINT ck_quiz_attempts_state CHECK (
        (status = 'IN_PROGRESS' AND score IS NULL AND correct_count IS NULL AND submitted_at IS NULL)
        OR
        (status = 'SUBMITTED' AND score IS NOT NULL AND correct_count IS NOT NULL AND submitted_at IS NOT NULL)
    ),
    ADD CONSTRAINT ck_quiz_attempts_score CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    ADD CONSTRAINT ck_quiz_attempts_counts CHECK (
        total_questions > 0 AND (correct_count IS NULL OR (correct_count >= 0 AND correct_count <= total_questions))
    );
