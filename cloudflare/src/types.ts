export interface Env {
  ADMIN_TOKEN: string;
  MEDIA_WORKER_TOKEN: string;
  CLOUDINARY_CLOUD_NAME: string;
  CLOUDINARY_API_KEY: string;
  CLOUDINARY_API_SECRET: string;
  CLOUDINARY_PREFIX: string;
  SUPABASE_URL: string;
  SUPABASE_KEY: string;
  CIRCLECI_TOKEN: string;
  CIRCLECI_PROJECT_SLUG: string;
  CIRCLECI_PIPELINE_DEFINITION_ID: string;
  CIRCLECI_BRANCH: string;
  CORS_ORIGINS: string;
  ENABLE_MEDIA_PILOT: string;
  RENDER_API_ORIGIN?: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  GOOGLE_REDIRECT_URI: string;
  META_APP_ID: string;
  META_APP_SECRET: string;
  META_REDIRECT_URI: string;
  META_API_VERSION: string;
  OAUTH_ENCRYPTION_KEY?: string;
  PUBLISH_WORKFLOW: Workflow<{publicationId:string;segment?:number}>;
  EDITING_WORKFLOW: Workflow<import('./editing').EditParams>;
  MEDIA_WORKFLOW: Workflow<{taskId: string;notifyGeneration?:string;notifyEditing?:string}>;
  GENERATION_WORKFLOW: Workflow<{videoId: string;segment?:number;runKey?:string}>;
  GENERATION_STAGE: Workflow<import('./stages').StageParams>;
}

export interface Asset {sha256: string; url?: string; key?: string;}
export interface Manifest {
  version: number;
  operation: 'assemble' | 'remix' | 'prepare_audio' | 'assemble_script';
  files: Record<string, Asset>;
  settings: Record<string, any>;
  images?: string[];
  [key: string]: any;
}
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function taskId(value: string): string {
  if (!/^[a-f0-9]{32}$/.test(value)) throw new ApiError(400, 'Expected 32 lowercase hexadecimal characters');
  return value;
}
