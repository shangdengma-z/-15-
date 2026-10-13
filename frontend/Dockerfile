FROM node:20-alpine AS build
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install
COPY frontend/ .
ARG VITE_BMAP_AK
ENV VITE_BMAP_AK=$VITE_BMAP_AK
RUN npm run build

FROM nginx:alpine
COPY --from=build /fe/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
