# deploy_secrets.ps1
# This script reads secrets from the .env file and deploys them to Kubernetes.

$envFile = ".env"
$namespace = "resume-agent"
$secretName = "resume-app-secrets"

if (-not (Test-Path $envFile)) {
    Write-Error "Could not find $envFile file in the current directory."
    exit 1
}

Write-Host "Loading secrets from $envFile..."

# Load .env file into a hashtable
$secrets = @{}
Get-Content $envFile | ForEach-Object {
    $item = $_.Trim()
    if ($item -and -not $item.StartsWith("#") -and $item.Contains("=")) {
        $key, $value = $item -split "=", 2
        # Remove surrounding quotes if present
        $value = $value.Trim().Trim('"').Trim("'")
        $secrets[$key.Trim()] = $value
    }
}

$googleApiKey = $secrets["GOOGLE_API_KEY"]
$nextAuthSecret = $secrets["NEXTAUTH_SECRET"]
$internalApiKey = $secrets["INTERNAL_API_KEY"]

if (-not $googleApiKey -or -not $nextAuthSecret -or -not $internalApiKey) {
    Write-Error "Missing required keys in .env file (GOOGLE_API_KEY, NEXTAUTH_SECRET, or INTERNAL_API_KEY)."
    exit 1
}

Write-Host "Deploying secret '$secretName' to namespace '$namespace'..."

# Create the secret using kubectl (upsert pattern)
kubectl create secret opaque $secretName `
    --namespace=$namespace `
    --from-literal=GOOGLE_API_KEY=$googleApiKey `
    --from-literal=NEXTAUTH_SECRET=$nextAuthSecret `
    --from-literal=INTERNAL_API_KEY=$internalApiKey `
    --dry-run=client -o yaml | kubectl apply -f -

Write-Host "Successfully deployed secrets!"
