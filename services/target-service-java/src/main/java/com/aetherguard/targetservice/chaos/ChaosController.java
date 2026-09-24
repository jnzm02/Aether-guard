package com.aetherguard.targetservice.chaos;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

/**
 * HTTP surface for the chaos failure modes. Every numeric parameter is
 * range-validated; an out-of-range value yields HTTP 400 (mirroring the Go
 * service's {@code queryInt} sentinel behaviour).
 */
@RestController
@RequestMapping("/chaos")
public class ChaosController {

    private final ChaosService chaos;

    public ChaosController(ChaosService chaos) {
        this.chaos = chaos;
    }

    @RequestMapping(value = "/memleak", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> memleak(@RequestParam(defaultValue = "50") int mb) {
        validateRange("mb", mb, 1, 4096);
        return chaos.memleak(mb);
    }

    @RequestMapping(value = "/cpu", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> cpu(@RequestParam(defaultValue = "1") int cores,
                                   @RequestParam(defaultValue = "30000") int ms) {
        int maxCores = Runtime.getRuntime().availableProcessors() * 4;
        validateRange("cores", cores, 1, maxCores);
        validateRange("ms", ms, 100, 300_000);
        return chaos.cpuSpike(cores, ms);
    }

    @RequestMapping(value = "/latency", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> latency(@RequestParam(defaultValue = "2000") int ms) {
        validateRange("ms", ms, 0, 30_000);
        return chaos.latency(ms);
    }

    @RequestMapping(value = "/error", method = {RequestMethod.GET, RequestMethod.POST})
    public ResponseEntity<Map<String, Object>> error(@RequestParam(defaultValue = "1.0") double rate) {
        if (rate < 0.0 || rate > 1.0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "invalid 'rate' parameter — must be 0.0..1.0");
        }
        if (chaos.shouldInjectError(rate)) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(chaos.errorBody());
        }
        return ResponseEntity.ok(chaos.noErrorBody(rate));
    }

    @RequestMapping(value = "/thread-leak", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> threadLeak(@RequestParam(defaultValue = "100") int count,
                                          @RequestParam(defaultValue = "0") int duration,
                                          @RequestParam(defaultValue = "false") boolean hard) {
        validateRange("count", count, 1, 10_000);
        validateRange("duration", duration, 0, 3600);
        return chaos.threadLeak(count, duration, hard);
    }

    @RequestMapping(value = "/gc-pressure", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> gcPressure(@RequestParam(name = "duration_seconds", defaultValue = "30") int durationSeconds) {
        validateRange("duration_seconds", durationSeconds, 1, 300);
        return chaos.gcPressure(durationSeconds);
    }

    @RequestMapping(value = "/status", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> status() {
        return chaos.status();
    }

    @RequestMapping(value = "/reset", method = {RequestMethod.GET, RequestMethod.POST})
    public Map<String, Object> reset() {
        return chaos.reset();
    }

    private static void validateRange(String name, int value, int min, int max) {
        if (value < min || value > max) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "invalid '" + name + "' parameter — must be " + min + ".." + max);
        }
    }
}
