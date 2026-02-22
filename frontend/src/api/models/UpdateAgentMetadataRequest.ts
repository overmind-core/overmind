/* tslint:disable */
/* eslint-disable */

/**
 * Request body for updating an agent's display name and/or tags.
 * @export
 * @interface UpdateAgentMetadataRequest
 */
export interface UpdateAgentMetadataRequest {
    /**
     * Custom display name (3–255 characters).
     * @type {string}
     * @memberof UpdateAgentMetadataRequest
     */
    name?: string | null;
    /**
     * List of category tags, e.g. ["HR", "financial"]. Replaces existing tags.
     * @type {Array<string>}
     * @memberof UpdateAgentMetadataRequest
     */
    tags?: Array<string> | null;
}

export function UpdateAgentMetadataRequestFromJSON(json: any): UpdateAgentMetadataRequest {
    return UpdateAgentMetadataRequestFromJSONTyped(json, false);
}

export function UpdateAgentMetadataRequestFromJSONTyped(json: any, _ignoreDiscriminator: boolean): UpdateAgentMetadataRequest {
    if (json == null) return json;
    return {
        'name': json['name'] == null ? undefined : json['name'],
        'tags': json['tags'] == null ? undefined : json['tags'],
    };
}

export function UpdateAgentMetadataRequestToJSON(json: any): UpdateAgentMetadataRequest {
    return UpdateAgentMetadataRequestToJSONTyped(json, false);
}

export function UpdateAgentMetadataRequestToJSONTyped(value?: UpdateAgentMetadataRequest | null, _ignoreDiscriminator: boolean = false): any {
    if (value == null) return value;
    return {
        'name': value['name'],
        'tags': value['tags'],
    };
}
