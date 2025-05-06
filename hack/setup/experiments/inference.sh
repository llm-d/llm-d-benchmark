cd ..
echo $LLMDBENCH_OPENSHIFT_HOST
echo $LLMDBENCH_OPENSHIFT_NAMESPACE
./deploy.sh --step 00
./deploy.sh --step 01
./deploy.sh --step 02
./deploy.sh --step 03
./deploy.sh --step 04
./deploy.sh --step 05
# ./deploy.sh --step 06
# ./deploy.sh --step 07
./deploy.sh --step 08
./deploy.sh --step 09
./deploy.sh --step 10
./deploy.sh --step 11