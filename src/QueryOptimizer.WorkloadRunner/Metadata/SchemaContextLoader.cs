using System.Text.Json;
using Npgsql;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private static async Task<SchemaContextPayload> LoadSchemaContextAsync(
        NpgsqlConnection connection,
        IReadOnlyList<string> tablesReferenced,
        CancellationToken cancellationToken)
    {
        var requestedTables = tablesReferenced
            .Select(NormalizeIdentifier)
            .Where(table => !string.IsNullOrWhiteSpace(table))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(table => table, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedTables.Length == 0)
        {
            return new SchemaContextPayload("no-referenced-tables", []);
        }

        var tables = new SortedDictionary<string, MutableSchemaTable>(StringComparer.OrdinalIgnoreCase);
        await LoadSchemaColumnsAsync(connection, requestedTables, tables, cancellationToken);
        await LoadSchemaIndexesAsync(connection, requestedTables, tables, cancellationToken);
        await LoadSchemaConstraintsAsync(connection, requestedTables, tables, cancellationToken);

        var payloadTables = tables.Values
            .Select(table => table.ToPayload())
            .ToArray();
        var snapshotJson = JsonSerializer.Serialize(payloadTables, JsonOptions);
        return new SchemaContextPayload(ComputeSha256(snapshotJson), payloadTables);
    }

    private static async Task LoadSchemaColumnsAsync(
        NpgsqlConnection connection,
        string[] requestedTables,
        SortedDictionary<string, MutableSchemaTable> tables,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                n.nspname AS table_schema,
                c.relname AS table_name,
                GREATEST(c.reltuples, 0)::bigint AS row_estimate,
                a.attname AS column_name,
                pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                NOT a.attnotnull AS nullable,
                a.attnum AS ordinal_position
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND lower(c.relname) = ANY(@tableNames)
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY n.nspname, c.relname, a.attnum;
            """;
        command.Parameters.AddWithValue("tableNames", requestedTables.Select(table => table.ToLowerInvariant()).ToArray());

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var schema = reader.GetString(0);
            var tableName = reader.GetString(1);
            var table = GetOrAddTable(tables, schema, tableName, reader.GetInt64(2));
            table.Columns.Add(
                new SchemaColumnContext(
                    reader.GetString(3),
                    reader.GetString(4),
                    reader.GetBoolean(5),
                    reader.GetInt32(6)));
        }
    }

    private static async Task LoadSchemaIndexesAsync(
        NpgsqlConnection connection,
        string[] requestedTables,
        SortedDictionary<string, MutableSchemaTable> tables,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                n.nspname AS table_schema,
                tbl.relname AS table_name,
                idx.relname AS index_name,
                am.amname AS access_method,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                COALESCE(
                    array_agg(att.attname ORDER BY key_position.ordinality)
                        FILTER (WHERE att.attname IS NOT NULL),
                    ARRAY[]::text[]
                ) AS columns,
                pg_get_indexdef(ix.indexrelid) AS definition
            FROM pg_index ix
            JOIN pg_class tbl ON tbl.oid = ix.indrelid
            JOIN pg_namespace n ON n.oid = tbl.relnamespace
            JOIN pg_class idx ON idx.oid = ix.indexrelid
            JOIN pg_am am ON am.oid = idx.relam
            LEFT JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS key_position(attnum, ordinality)
                ON key_position.attnum > 0
            LEFT JOIN pg_attribute att ON att.attrelid = tbl.oid AND att.attnum = key_position.attnum
            WHERE tbl.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND lower(tbl.relname) = ANY(@tableNames)
            GROUP BY n.nspname, tbl.relname, idx.relname, am.amname, ix.indisunique, ix.indisprimary, ix.indexrelid
            ORDER BY n.nspname, tbl.relname, idx.relname;
            """;
        command.Parameters.AddWithValue("tableNames", requestedTables.Select(table => table.ToLowerInvariant()).ToArray());

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var table = GetOrAddTable(tables, reader.GetString(0), reader.GetString(1), rowEstimate: null);
            table.Indexes.Add(
                new SchemaIndexContext(
                    reader.GetString(2),
                    reader.GetString(3),
                    reader.GetBoolean(4),
                    reader.GetBoolean(5),
                    reader.GetFieldValue<string[]>(6),
                    reader.GetString(7)));
        }
    }

    private static async Task LoadSchemaConstraintsAsync(
        NpgsqlConnection connection,
        string[] requestedTables,
        SortedDictionary<string, MutableSchemaTable> tables,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                n.nspname AS table_schema,
                tbl.relname AS table_name,
                con.conname AS constraint_name,
                CASE con.contype
                    WHEN 'p' THEN 'primary_key'
                    WHEN 'u' THEN 'unique'
                    WHEN 'f' THEN 'foreign_key'
                    ELSE con.contype::text
                END AS constraint_type,
                COALESCE((
                    SELECT array_agg(att.attname ORDER BY con_column.ordinality)
                    FROM unnest(con.conkey) WITH ORDINALITY AS con_column(attnum, ordinality)
                    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con_column.attnum
                ), ARRAY[]::text[]) AS columns,
                ref.relname AS referenced_table,
                COALESCE((
                    SELECT array_agg(att.attname ORDER BY ref_column.ordinality)
                    FROM unnest(con.confkey) WITH ORDINALITY AS ref_column(attnum, ordinality)
                    JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = ref_column.attnum
                ), ARRAY[]::text[]) AS referenced_columns
            FROM pg_constraint con
            JOIN pg_class tbl ON tbl.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = tbl.relnamespace
            LEFT JOIN pg_class ref ON ref.oid = con.confrelid
            WHERE con.contype IN ('p', 'u', 'f')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND lower(tbl.relname) = ANY(@tableNames)
            ORDER BY n.nspname, tbl.relname, con.conname;
            """;
        command.Parameters.AddWithValue("tableNames", requestedTables.Select(table => table.ToLowerInvariant()).ToArray());

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var referencedTable = reader.IsDBNull(5) ? null : reader.GetString(5);
            var table = GetOrAddTable(tables, reader.GetString(0), reader.GetString(1), rowEstimate: null);
            table.Constraints.Add(
                new SchemaConstraintContext(
                    reader.GetString(2),
                    reader.GetString(3),
                    reader.GetFieldValue<string[]>(4),
                    referencedTable,
                    reader.GetFieldValue<string[]>(6)));
        }
    }

    private static MutableSchemaTable GetOrAddTable(
        SortedDictionary<string, MutableSchemaTable> tables,
        string schema,
        string tableName,
        long? rowEstimate)
    {
        var key = $"{schema}.{tableName}";
        if (!tables.TryGetValue(key, out var table))
        {
            table = new MutableSchemaTable(schema, tableName);
            tables.Add(key, table);
        }

        if (rowEstimate.HasValue)
        {
            table.RowEstimate = rowEstimate.Value;
        }

        return table;
    }
}

internal sealed record SchemaContextPayload(
    string SnapshotHash,
    IReadOnlyList<SchemaTableContext> Tables);

internal sealed record SchemaTableContext(
    string Schema,
    string Name,
    long? RowEstimate,
    IReadOnlyList<SchemaColumnContext> Columns,
    IReadOnlyList<SchemaIndexContext> Indexes,
    IReadOnlyList<SchemaConstraintContext> Constraints);

internal sealed record SchemaColumnContext(
    string Name,
    string Type,
    bool Nullable,
    int OrdinalPosition);

internal sealed record SchemaIndexContext(
    string Name,
    string AccessMethod,
    bool Unique,
    bool Primary,
    IReadOnlyList<string> Columns,
    string Definition);

internal sealed record SchemaConstraintContext(
    string Name,
    string Type,
    IReadOnlyList<string> Columns,
    string? ReferencedTable,
    IReadOnlyList<string> ReferencedColumns);

internal sealed class MutableSchemaTable
{
    public MutableSchemaTable(string schema, string name)
    {
        Schema = schema;
        Name = name;
    }

    public string Schema { get; }
    public string Name { get; }
    public long? RowEstimate { get; set; }
    public List<SchemaColumnContext> Columns { get; } = [];
    public List<SchemaIndexContext> Indexes { get; } = [];
    public List<SchemaConstraintContext> Constraints { get; } = [];

    public SchemaTableContext ToPayload()
    {
        return new SchemaTableContext(
            Schema,
            Name,
            RowEstimate,
            Columns.OrderBy(column => column.OrdinalPosition).ToArray(),
            Indexes.OrderBy(index => index.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Constraints.OrderBy(constraint => constraint.Name, StringComparer.OrdinalIgnoreCase).ToArray());
    }
}
