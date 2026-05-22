SELECT d.l_orderkey
FROM (
  SELECT l_orderkey, l_partkey
  FROM lineitem
) d;
