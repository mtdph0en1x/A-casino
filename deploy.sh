
set -euo pipefail


RESOURCE_GROUP="rg-telegram-bot"
LOCATION="northeurope"           
ACR_NAME="tgbotregistry$RANDOM" 
APP_ENV_NAME="telegram-bot-env"
APP_NAME="telegram-bot"
BOT_TOKEN="${BOT_TOKEN:? export BOT_TOKEN}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-$(openssl rand -hex 16)}"


echo " Creating Resource Group: $RESOURCE_GROUP"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo " Creating Azure Container Registry: $ACR_NAME"
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true \
  --output none

echo "  Creating Container Apps Environment: $APP_ENV_NAME"
az containerapp env create \
  --name "$APP_ENV_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

echo " Building & pushing Docker image to ACR"
az acr build \
  --registry "$ACR_NAME" \
  --image "$APP_NAME:latest" \
  ./bot

# Fetch ACR credentials for the container app
ACR_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)

echo " Creating Container App: $APP_NAME"
az containerapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$APP_ENV_NAME" \
  --image "$ACR_SERVER/$APP_NAME:latest" \
  --registry-server "$ACR_SERVER" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8080 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.25 \
  --memory 0.5Gi \
  --secrets \
    bot-token="$BOT_TOKEN" \
    webhook-secret="$WEBHOOK_SECRET" \
  --env-vars \
    BOT_TOKEN=secretref:bot-token \
    WEBHOOK_SECRET=secretref:webhook-secret \
    WEBHOOK_URL="" \
  --output none

# Get the public URL assigned by Azure
APP_URL=$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
APP_URL="https://$APP_URL"

echo " App URL: $APP_URL"

# Update WEBHOOK_URL 
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars "WEBHOOK_URL=$APP_URL" \
  --output none

