# deploy_storage_secrets.ps1
# This script reads storage secrets from storage/.env and the root .env, then deploys them to Kubernetes.

$rootEnvFile = ".env"
$storageEnvFile = "storage/.env"
$namespace = "resume-agent"
$secretName = "storage-s3-secret"

# Simple function to parse env files
function Parse-EnvFile {
    param([string]$FilePath)
    $data = @{}
    if (Test-Path $FilePath) {
        Get-Content $FilePath | ForEach-Object {
            $item = $_.Trim()
            if ($item -and -not $item.StartsWith("#") -and $item.Contains("=")) {
                $key, $value = $item -split "=", 2
                $value = $value.Trim().Trim('"').Trim("'")
                $data[$key.Trim()] = $value
            }
        }
    }
    return $data
}

Write-Host "Loading secrets..."
$rootSecrets = Parse-EnvFile -FilePath $rootEnvFile
$storageSecrets = Parse-EnvFile -FilePath $storageEnvFile

$s3AccessKey = $storageSecrets["S3_ACCESS_KEY"]
$s3SecretKey = $storageSecrets["S3_SECRET_KEY"]
$internalApiKey = $rootSecrets["INTERNAL_API_KEY"]

if (-not $s3AccessKey -or -not $s3SecretKey -or -not $internalApiKey) {
    Write-Error "Missing required keys in .env files (S3_ACCESS_KEY, S3_SECRET_KEY, or INTERNAL_API_KEY)."
    exit 1
}

Write-Host "Deploying secret '$secretName' to namespace '$namespace'..."

# Create the secret using kubectl (upsert pattern)
kubectl create secret opaque $secretName `
    --namespace=$namespace `
    --from-literal=S3_ACCESS_KEY=$s3AccessKey `
    --from-literal=S3_SECRET_KEY=$s3SecretKey `
    --from-literal=INTERNAL_API_KEY=$internalApiKey `
    --dry-run=client -o yaml | kubectl apply -f -

Write-Host "Successfully deployed storage secrets!"
