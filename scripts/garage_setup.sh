BUCKET="resume-agent-bucket"
KEY="agent-storage-key"

kubectl exec -it -n garage garage-0 -- ./garage bucket create $BUCKET
kubectl exec -it -n garage garage-0 -- ./garage key create $KEY
kubectl exec -it -n garage garage-0 -- ./garage bucket allow $BUCKET --read --write --owner --key $KEY