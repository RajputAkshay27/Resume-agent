import { S3Client, CreateBucketCommand, HeadBucketCommand, CopyObjectCommand, DeleteObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";

const endpoint = process.env.S3_ENDPOINT || "http://localhost:3900";
const region = process.env.S3_REGION || "garage";
const bucketParams = {
  endpoint: endpoint.startsWith("http") ? endpoint : `http://${endpoint}`,
  region: region,
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY || "",
    secretAccessKey: process.env.S3_SECRET_KEY || "",
  },
  forcePathStyle: true, // Needed for Garage and Minio
};

export const s3Client = new S3Client(bucketParams);

export const ensureBucketExists = async (bucketName: string) => {
  try {
    await s3Client.send(new HeadBucketCommand({ Bucket: bucketName }));
  } catch (error: any) {
    if (error.name === "NotFound" || error.$metadata?.httpStatusCode === 404) {
      console.log(`Bucket ${bucketName} not found. Creating...`);
      await s3Client.send(new CreateBucketCommand({ Bucket: bucketName }));
      console.log(`Bucket ${bucketName} created successfully.`);
    } else {
      console.error("Error checking bucket:", error);
    }
  }
};

export const copyS3Object = async (bucket: string, sourceKey: string, targetKey: string) => {
  try {
    const command = new CopyObjectCommand({
      Bucket: bucket,
      CopySource: encodeURI(`${bucket}/${sourceKey}`),
      Key: targetKey,
    });
    return await s3Client.send(command);
  } catch (error) {
    console.error(`S3 Copy error (${sourceKey} -> ${targetKey}):`, error);
    throw error;
  }
};

export const deleteS3Object = async (bucket: string, key: string) => {
  try {
    const command = new DeleteObjectCommand({
      Bucket: bucket,
      Key: key,
    });
    return await s3Client.send(command);
  } catch (error) {
    console.error(`S3 Delete error (${key}):`, error);
    throw error;
  }
};

export const headS3Object = async (bucket: string, key: string) => {
  try {
    const command = new HeadObjectCommand({
      Bucket: bucket,
      Key: key,
    });
    return await s3Client.send(command);
  } catch (error: any) {
    if (error.name === "NotFound" || error.$metadata?.httpStatusCode === 404) {
      return null;
    }
    throw error;
  }
};
