import apiClient from "@/client";
import type { CapabilityList, PaginatedCapabilityListList } from "@/openapi";

/** DRF max_page_size for the capabilities list (see api/config.py PageNumberPagination). */
const CAPABILITIES_API_PAGE_SIZE = 100;
const CAPABILITIES_MAX_PAGES = 50;

export const fetchAllCapabilities = async (
  projectId: string
): Promise<PaginatedCapabilityListList> => {
  const results: CapabilityList[] = [];
  let page = 1;
  let count = 0;

  while (page <= CAPABILITIES_MAX_PAGES) {
    const data = await apiClient.capabilities.capabilitiesList({
      page,
      pageSize: CAPABILITIES_API_PAGE_SIZE,
      project: projectId,
    });
    count = data.count;
    results.push(...data.results);
    if (!data.next || results.length >= count || data.results.length === 0) break;
    page += 1;
  }

  return { count, next: null, previous: null, results };
};
