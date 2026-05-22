SELECT l.l_orderkey
FROM lineitem l, partsupp p
WHERE l.l_partkey = p.ps_partkey
  AND p.ps_supplycost > $1;
