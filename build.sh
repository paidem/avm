#!/bin/bash

# Try to get the exact tag
if TAG=$(git describe --exact-match --tags 2>/dev/null); then
    echo "Building on tag: $TAG"
    IMAGE=paidem/avm
    
    # Build the Docker image with both tag and latest
    docker build -t $IMAGE:$TAG -t $IMAGE:latest .
    
    # Push both versions to registry
    docker push $IMAGE:$TAG
    docker push $IMAGE:latest
    
    echo "Successfully built and pushed $IMAGE:$TAG and $IMAGE:latest"
else
    echo "Not on an exact tag, skipping build and push"
    exit 1
fi
