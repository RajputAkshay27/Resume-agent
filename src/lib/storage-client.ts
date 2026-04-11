export interface StorageUploadResponse {
  key: string;
  success: boolean;
}

export interface StorageListResponse {
  files: Array<{
    key: string;
    size: number;
    last_modified: string;
  }>;
}

const STORAGE_SERVICE_URL = process.env.STORAGE_SERVICE_URL || "http://storage:8001";
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || "default_secret_key_change_me";

class StorageClient {
  private url: string;
  private apiKey: string;

  constructor(url: string, apiKey: string) {
    // Sanitize URL: remove potential quotes from .env and ensure protocol
    const sanitizedUrl = url.replace(/['"]/g, "").trim();
    this.url = sanitizedUrl.startsWith("http") ? sanitizedUrl : `http://${sanitizedUrl}`;
    // Sanitize API key: remove potential quotes from .env
    this.apiKey = apiKey.replace(/['"]/g, "").trim();
  }

  private async request(path: string, options: RequestInit = {}) {
    const response = await fetch(`${this.url}${path}`, {
      ...options,
      headers: {
        ...options.headers,
        "X-API-Key": this.apiKey,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Storage Service Error (${response.status}): ${errorText}`);
    }

    return response;
  }

  async upload(file: File | Blob, key?: string): Promise<StorageUploadResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (key) formData.append("key", key);

    const response = await this.request("/upload", {
      method: "POST",
      body: formData,
    });
    return response.json();
  }

  async uploadBase64(key: string, contentB64: string, contentType?: string): Promise<StorageUploadResponse> {
    const response = await this.request("/upload-base64", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        key,
        content_b64: contentB64,
        content_type: contentType,
      }),
    });
    return response.json();
  }

  async download(key: string): Promise<Response> {
    return this.request(`/download/${encodeURIComponent(key)}`);
  }

  async delete(key: string): Promise<{ success: boolean }> {
    const response = await this.request(`/delete/${encodeURIComponent(key)}`, {
      method: "DELETE",
    });
    return response.json();
  }

  async copy(sourceKey: string, targetKey: string): Promise<{ success: boolean; target_key: string }> {
    const response = await this.request("/copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_key: sourceKey,
        target_key: targetKey,
      }),
    });
    return response.json();
  }

  async list(prefix: string = ""): Promise<StorageListResponse> {
    const response = await this.request(`/list/${encodeURIComponent(prefix)}`);
    return response.json();
  }
}

export const storageClient = new StorageClient(STORAGE_SERVICE_URL, INTERNAL_API_KEY);
