"""
pid_controller.py — PID Controller for smooth aiming movement.
"""

from __future__ import annotations


class PIDController:
    """PID Controller - used for smooth aiming movement

    Implements Proportional-Integral-Derivative (PID) control algorithm for calculating mouse movement.
    Supports independent X/Y axis settings and includes dynamic P-parameter adjustment.

    Attributes:
        Kp: Proportional coefficient, controls reaction speed
        Ki: Integral coefficient, corrects static error
        Kd: Derivative coefficient, suppresses jitter and overshoot
    """

    def __init__(self, Kp: float, Ki: float, Kd: float) -> None:
        self.Kp = Kp  # Proportional
        self.Ki = Ki  # Integral
        self.Kd = Kd  # Derivative
        self.reset()

    def reset(self) -> None:
        """Reset controller state"""
        self.integral: float = 0.0
        self.previous_error: float = 0.0

    def update(self, error: float) -> float:
        """
        Calculates control output based on current error

        Args:
            error: Current error (e.g., target_x - current_x)

        Returns:
            Control amount (e.g., amount mouse should move)
        """
        # Integral term (with anti-windup clamping)
        self.integral += error
        self.integral = max(-1000.0, min(1000.0, self.integral))

        # Derivative term
        derivative = error - self.previous_error

        # Adjust P parameter response curve
        adjusted_kp = self._calculate_adjusted_kp(self.Kp)

        # Calculate output
        output = (adjusted_kp * error) + (self.Ki * self.integral) + (self.Kd * derivative)

        # Update previous error
        self.previous_error = error

        return output

    def _calculate_adjusted_kp(self, kp: float) -> float:
        """Calculate dynamically adjusted P parameter

        Implements non-linear P parameter response curve:
        - 0% ~ 50%: Linear growth, maintains original proportion
        - 50% ~ 100%: Accelerated growth, eventually scaling to 200%

        This design allows for smoother low sensitivity and more aggressive high sensitivity.

        Args:
            kp: Original P parameter value (0.0 ~ 1.0)

        Returns:
            Adjusted P parameter value (0.0 ~ 2.0)
        """
        if kp <= 0.5:
            return kp
        else:
            # When kp=0.5, output=0.5; when kp=1.0, output=2.0
            return 0.5 + (kp - 0.5) * 3.0
