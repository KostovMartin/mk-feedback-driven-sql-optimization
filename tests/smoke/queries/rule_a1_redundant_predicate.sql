SELECT l_orderkey
FROM lineitem
WHERE l_partkey > 250
  AND l_partkey > 10;
