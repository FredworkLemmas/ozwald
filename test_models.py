from orchestration.models import (
    ServiceDefinition,
    ServiceDefinitionProfile,
    ServiceDefinitionVariety,
)

p1 = ServiceDefinitionProfile(name="p1", description="desc1")
v1 = ServiceDefinitionVariety(image="image1")

sd = ServiceDefinition(
    service_name="myservice",
    type="container",
    profiles={"p1": p1},
    varieties={"v1": v1},
)

print("As dict:")
sd_dict = sd.model_dump()
print(sd_dict)

print("\nProfiles type:", type(sd_dict["profiles"]))
print("Varieties type:", type(sd_dict["varieties"]))

profiles = sd_dict.get("profiles", [])
print("\nIterating profiles:")
for p in profiles:
    print(f"p: {p}, type: {type(p)}")
    try:
        print(f"p.get('name'): {p.get('name')}")
    except Exception as e:
        print(f"Error: {e}")
