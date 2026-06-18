FROM node:18-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

EXPOSE 5173

# Dev server — the vite proxy forwards /api to VITE_API_PROXY_TARGET
# (set by docker-compose to http://backend:8000). Production deployments
# should swap this for a multi-stage build + nginx serving dist/.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
