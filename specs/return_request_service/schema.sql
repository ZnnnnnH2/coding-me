CREATE TABLE return_requests (
    id INTEGER PRIMARY KEY,
    request_number TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('submitted', 'approved', 'rejected', 'received', 'closed')),
    reason_code TEXT NOT NULL CHECK (
        reason_code IN ('damaged', 'wrong_item', 'not_as_described', 'customer_remorse')
    ),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMP NOT NULL,
    review_decision TEXT NULL CHECK (review_decision IN ('approved', 'rejected') OR review_decision IS NULL),
    review_note TEXT NULL,
    reviewed_by TEXT NULL,
    reviewed_at TIMESTAMP NULL,
    warehouse_receipt_no TEXT NULL,
    received_by TEXT NULL,
    received_at TIMESTAMP NULL,
    closed_by TEXT NULL,
    closed_at TIMESTAMP NULL,
    close_note TEXT NULL,
    CHECK (
        (reviewed_at IS NULL AND reviewed_by IS NULL)
        OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)
    ),
    CHECK (
        (received_at IS NULL AND received_by IS NULL AND warehouse_receipt_no IS NULL)
        OR (received_at IS NOT NULL AND received_by IS NOT NULL AND warehouse_receipt_no IS NOT NULL)
    ),
    CHECK (
        (closed_at IS NULL AND closed_by IS NULL)
        OR (closed_at IS NOT NULL AND closed_by IS NOT NULL)
    )
);

CREATE TABLE return_request_items (
    id INTEGER PRIMARY KEY,
    return_request_id INTEGER NOT NULL REFERENCES return_requests(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    product_title TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
    approved_quantity INTEGER NULL CHECK (
        approved_quantity IS NULL OR approved_quantity BETWEEN 0 AND requested_quantity
    ),
    received_quantity INTEGER NOT NULL DEFAULT 0 CHECK (received_quantity >= 0),
    line_status TEXT NOT NULL CHECK (line_status IN ('submitted', 'approved', 'rejected', 'received', 'closed')),
    line_reason_text TEXT NULL,
    CHECK (received_quantity <= COALESCE(approved_quantity, requested_quantity))
);

CREATE INDEX idx_return_requests_status_requested_at
    ON return_requests (status, requested_at);

CREATE INDEX idx_return_request_items_request_id
    ON return_request_items (return_request_id);
