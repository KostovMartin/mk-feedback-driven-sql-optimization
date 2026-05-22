SELECT l_orderkey
FROM lineitem
WHERE l_partkey > $1
  AND TRUE;
