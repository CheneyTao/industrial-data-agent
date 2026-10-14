-- Idempotent repair for demo volumes initialized by a mysql client that
-- advertised latin1 while reading the UTF-8 seed files.
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE users
  MODIFY id INT NOT NULL AUTO_INCREMENT COMMENT '用户ID，主键自增',
  MODIFY username VARCHAR(50) NOT NULL COMMENT '用户名',
  MODIFY email VARCHAR(100) NOT NULL COMMENT '用户邮箱',
  MODIFY created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '用户注册时间',
  COMMENT = '用户表';

ALTER TABLE products
  MODIFY id INT NOT NULL AUTO_INCREMENT COMMENT '商品ID，主键自增',
  MODIFY name VARCHAR(100) NOT NULL COMMENT '商品名称',
  MODIFY price DECIMAL(10,2) NOT NULL COMMENT '商品单价',
  MODIFY stock INT NOT NULL COMMENT '商品库存数量',
  MODIFY created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '商品上架时间',
  COMMENT = '商品表';

ALTER TABLE orders
  MODIFY id INT NOT NULL AUTO_INCREMENT COMMENT '订单ID，主键自增',
  MODIFY user_id INT NOT NULL COMMENT '下单用户ID',
  MODIFY order_date DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '下单时间',
  MODIFY total_amount DECIMAL(10,2) NOT NULL COMMENT '订单总金额',
  MODIFY status VARCHAR(20) DEFAULT 'pending' COMMENT '订单状态（pending/completed/cancelled等）',
  COMMENT = '订单表';

ALTER TABLE order_items
  MODIFY id INT NOT NULL AUTO_INCREMENT COMMENT '订单明细ID，主键自增',
  MODIFY order_id INT NOT NULL COMMENT '订单ID',
  MODIFY product_id INT NOT NULL COMMENT '商品ID',
  MODIFY quantity INT NOT NULL COMMENT '购买数量',
  MODIFY unit_price DECIMAL(10,2) NOT NULL COMMENT '下单时商品单价',
  COMMENT = '订单明细表';

ALTER TABLE categories
  MODIFY id INT NOT NULL AUTO_INCREMENT COMMENT '分类ID，主键自增',
  MODIFY name VARCHAR(50) NOT NULL COMMENT '分类名称',
  COMMENT = '商品分类表';

ALTER TABLE product_categories
  MODIFY product_id INT NOT NULL COMMENT '商品ID',
  MODIFY category_id INT NOT NULL COMMENT '分类ID',
  COMMENT = '商品与分类关联表';

UPDATE categories
SET name = CASE id
  WHEN 1 THEN '电子产品'
  WHEN 2 THEN '服装'
  WHEN 3 THEN '图书'
  WHEN 4 THEN '家居用品'
  WHEN 5 THEN '食品'
  ELSE name
END
WHERE id BETWEEN 1 AND 5;

UPDATE products
SET name = CASE id
  WHEN 1 THEN '智能手机'
  WHEN 2 THEN 'T恤衫'
  WHEN 3 THEN '小说'
  WHEN 4 THEN '咖啡机'
  WHEN 5 THEN '牛奶'
  WHEN 6 THEN '笔记本电脑'
  WHEN 7 THEN '沙发'
  WHEN 8 THEN '巧克力'
  WHEN 9 THEN '羽绒服'
  WHEN 10 THEN '历史书'
  ELSE name
END
WHERE id BETWEEN 1 AND 10;
