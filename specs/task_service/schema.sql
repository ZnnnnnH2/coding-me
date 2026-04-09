CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NULL,
    owner TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high')),
    due_date DATE NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NULL,
    updated_at TIMESTAMP NOT NULL,
    CHECK (length(trim(title)) BETWEEN 3 AND 120),
    CHECK (
        (completed = FALSE AND completed_at IS NULL)
        OR (completed = TRUE AND completed_at IS NOT NULL)
    )
);

CREATE INDEX idx_tasks_completed_priority
    ON tasks (completed, priority);

CREATE INDEX idx_tasks_due_date
    ON tasks (due_date);
