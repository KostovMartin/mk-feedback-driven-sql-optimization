SELECT l_partkey
FROM (
  SELECT l_partkey
  FROM lineitem
  GROUP BY l_partkey
) grouped
GROUP BY l_partkey;
