SELECT l_orderkey
FROM lineitem
WHERE l_partkey IN (
  SELECT ps_partkey
  FROM partsupp
  WHERE ps_supplycost > $1
);
