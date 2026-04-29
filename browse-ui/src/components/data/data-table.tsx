"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/data/empty-state";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type DataTableProps<TData, TValue> = {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  isLoading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
  onRowClick?: (row: TData) => void;
};

export function DataTable<TData, TValue>({
  columns,
  data,
  isLoading = false,
  emptyTitle = "No data",
  emptyDescription,
  className,
  onRowClick,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (!isLoading && data.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className={cn("bg-card rounded-xl border", className)}>
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                const sortIcon =
                  sorted === "asc" ? (
                    <ArrowUp className="size-3.5" />
                  ) : sorted === "desc" ? (
                    <ArrowDown className="size-3.5" />
                  ) : (
                    <ArrowUpDown className="size-3.5 opacity-50" />
                  );

                return (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : canSort ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 px-1"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sortIcon}
                      </Button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {isLoading
            ? Array.from({ length: 8 }).map((_, index) => (
                <TableRow key={`skeleton-${index}`}>
                  {columns.map((column, cellIndex) => (
                    <TableCell key={`${column.id ?? cellIndex}-${index}`}>
                      <div className="bg-muted h-4 w-full animate-pulse rounded" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className={onRowClick ? "cursor-pointer" : undefined}
                  onClick={() => onRowClick?.(row.original)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </div>
  );
}
