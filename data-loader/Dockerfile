FROM postgres:18.3

COPY load-smoke.sh /usr/local/bin/load-smoke.sh
RUN chmod +x /usr/local/bin/load-smoke.sh

ENTRYPOINT ["load-smoke.sh"]
