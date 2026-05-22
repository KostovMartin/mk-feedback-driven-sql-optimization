SELECT l_orderkey
FROM lineitem
WHERE (
  SELECT COUNT(*)
  FROM partsupp
  WHERE ps_supplycost > $1
    AND ps_partkey = l_partkey
) > 0;
