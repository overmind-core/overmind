# Production stage
FROM nginx:stable-alpine

# Copy the built static files (pre-built in GHA)
COPY dist /usr/share/nginx/html

# Copy the custom NGINX config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
