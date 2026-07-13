package org.hellorealworld.ping;

import java.util.Map;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;

@Path("/ping")
@Produces(MediaType.APPLICATION_JSON)
public class PingResource {

    @GET
    public Map<String, String> ping() {
        return Map.of("message", "pong");
    }
}
