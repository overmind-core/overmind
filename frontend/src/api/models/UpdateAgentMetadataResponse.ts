/* tslint:disable */
/* eslint-disable */

/**
 * Response after updating an agent's display name and/or tags.
 * @export
 * @interface UpdateAgentMetadataResponse
 */
export interface UpdateAgentMetadataResponse {
    /**
     * @type {string}
     * @memberof UpdateAgentMetadataResponse
     */
    slug: string;
    /**
     * @type {string}
     * @memberof UpdateAgentMetadataResponse
     */
    name: string;
    /**
     * @type {Array<string>}
     * @memberof UpdateAgentMetadataResponse
     */
    tags: Array<string>;
}

export function UpdateAgentMetadataResponseFromJSON(json: any): UpdateAgentMetadataResponse {
    return UpdateAgentMetadataResponseFromJSONTyped(json, false);
}

export function UpdateAgentMetadataResponseFromJSONTyped(json: any, _ignoreDiscriminator: boolean): UpdateAgentMetadataResponse {
    if (json == null) return json;
    return {
        'slug': json['slug'],
        'name': json['name'],
        'tags': json['tags'],
    };
}

export function UpdateAgentMetadataResponseToJSON(json: any): UpdateAgentMetadataResponse {
    return UpdateAgentMetadataResponseToJSONTyped(json, false);
}

export function UpdateAgentMetadataResponseToJSONTyped(value?: UpdateAgentMetadataResponse | null, _ignoreDiscriminator: boolean = false): any {
    if (value == null) return value;
    return {
        'slug': value['slug'],
        'name': value['name'],
        'tags': value['tags'],
    };
}
