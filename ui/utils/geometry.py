import math

def compute_radius(n, node_size, padding=100):
    if n > 1:
        min_radius = node_size / (2 * math.sin(math.pi / n))
    else:
        min_radius = 100
    return min_radius + padding

def circle_position(container_size, radius, index, total):
    angle = 2 * math.pi * index / total
    x = container_size/2 + radius * math.cos(angle)
    y = container_size/2 + radius * math.sin(angle)
    return x, y
