CREATE TABLE warehouse_dispatch_tasks (
    id INTEGER PRIMARY KEY,
    dispatch_reference TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    dock_code TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('normal', 'urgent')),
    scheduled_departure_at TIMESTAMP NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMP NULL,
    updated_at TIMESTAMP NOT NULL,
    CHECK (
        (completed = FALSE AND completed_at IS NULL)
        OR (completed = TRUE AND completed_at IS NOT NULL)
    )
);

CREATE INDEX idx_warehouse_dispatch_tasks_departure
    ON warehouse_dispatch_tasks (scheduled_departure_at);

CREATE INDEX idx_warehouse_dispatch_tasks_priority_departure
    ON warehouse_dispatch_tasks (priority, scheduled_departure_at);
