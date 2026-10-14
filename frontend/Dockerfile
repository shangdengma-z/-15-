FROM node:20-alpine AS build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
# npm ci 按 lockfile 精确还原依赖，保证评审构建出与本地一致的产物
RUN npm ci
COPY frontend/ .
ARG VITE_BMAP_AK
ENV VITE_BMAP_AK=$VITE_BMAP_AK
RUN npm run build

FROM nginx:alpine
COPY --from=build /fe/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
