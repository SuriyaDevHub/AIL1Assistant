# Builds the Review UI and serves the static bundle via nginx, proxying
# /api to the backend service name so the browser only ever talks to one
# origin (avoids CORS entirely inside docker-compose).
FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
