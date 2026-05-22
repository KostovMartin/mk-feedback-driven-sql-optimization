SELECT l_orderkey
FROM lineitem
WHERE NOT (l_partkey = $1);
