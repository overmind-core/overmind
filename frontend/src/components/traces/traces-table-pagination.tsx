import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { paginationFromPageLimit } from "@/lib/utils";

interface TracesTablePaginationProps {
  page: number;
  pageSize: number;
  count: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export function TracesTablePagination({
  count,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: TracesTablePaginationProps) {
  const pagination = paginationFromPageLimit({ count, page, pageSize });

  return (
    <div className="flex items-center justify-between px-2 py-4 sticky bottom-0 bg-background">
      <div className="text-muted-foreground flex-1 text-sm">
        {pagination.count > 0 ? (
          <>
            {pagination.startItem}-{pagination.endItem} of {pagination.total} row(s)
          </>
        ) : (
          "0 row(s)"
        )}
      </div>
      <div className="flex items-center space-x-6 lg:space-x-8">
        <div className="flex items-center space-x-2">
          <p className="text-sm font-medium">Rows per page</p>
          <Select onValueChange={(v) => onPageSizeChange(Number(v))} value={String(pageSize)}>
            <SelectTrigger className="h-8 w-[80px]">
              <SelectValue placeholder={pageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 25, 50, 100].map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex w-[120px] items-center justify-center text-sm font-medium">
          Page {page} of {pagination.totalPages}
        </div>
        <div className="flex items-center space-x-2">
          <Button
            className="size-8"
            disabled={!pagination.hasPrevious}
            onClick={() => onPageChange(page - 1)}
            size="icon"
            variant="outline"
          >
            <span className="sr-only">Go to previous page</span>
            <ChevronLeft className="size-4" />
          </Button>
          <Button
            className="size-8"
            disabled={!pagination.hasNext}
            onClick={() => onPageChange(page + 1)}
            size="icon"
            variant="outline"
          >
            <span className="sr-only">Go to next page</span>
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
