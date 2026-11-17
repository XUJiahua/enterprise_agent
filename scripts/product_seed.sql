-- Demo data for the v0.1 product page (apps/web/app/procurement). Admin only.
INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount, status, internal_note) VALUES
  ('tenant-a', 'po-100', 'alice', '研发笔记本电脑', 900, 'approved', 'supplier-bank-account'),
  ('tenant-a', 'po-101', 'alice', '会议室显示器', 300, 'approved', 'supplier-bank-account'),
  ('tenant-a', 'po-102', 'alice', '人体工学椅', 50, 'submitted', ''),
  ('tenant-b', 'po-200', 'dave', '机房服务器', 5000, 'approved', 'secret');
INSERT INTO procurement.receipt (tenant_id, purchase_id, amount, received_by) VALUES
  ('tenant-a', 'po-100', 900, 'alice'),
  ('tenant-a', 'po-101', 100, 'alice');
